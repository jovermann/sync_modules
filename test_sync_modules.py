import argparse
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("sync_modules.py")
SPEC = importlib.util.spec_from_file_location("sync_modules", SCRIPT)
sync_modules = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_modules)


class ConfigTests(unittest.TestCase):
    def test_missing_default_config_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory, ".sync_modules.toml")
            with mock.patch.object(sync_modules, "DEFAULT_CONFIG", config_path):
                config = sync_modules.load_config(config_path)
            self.assertEqual(config["workspace"], "github")
            self.assertEqual(config["jobs"], 0)
            self.assertIn("cpp", config["extensions"])
            self.assertIn(".venv", config["exclude"])
            self.assertEqual(
                config_path.read_text(), sync_modules.DEFAULT_CONFIG_CONTENT
            )

    def test_missing_custom_config_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory, "custom.toml")
            with self.assertRaisesRegex(RuntimeError, "Cannot load config"):
                sync_modules.load_config(config_path)
            self.assertFalse(config_path.exists())

    def test_default_config_is_valid(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml") as config_file:
            config_file.write(
                'workspace = "github"\njobs = 0\n'
                'extensions = ["c", "py"]\n'
                'exclude = ["old", "keep"]\n'
            )
            config_file.flush()
            config = sync_modules.load_config(config_file.name)
        self.assertEqual(config["workspace"], "github")
        self.assertIn("old", config["exclude"])
        self.assertIn("py", config["extensions"])

    def test_config_rejects_non_string_exclusion(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml") as config_file:
            config_file.write(
                'workspace = ".."\njobs = 1\n'
                'extensions = ["py"]\n'
                'exclude = [1]\n'
            )
            config_file.flush()
            with self.assertRaisesRegex(RuntimeError, "exclude"):
                sync_modules.load_config(config_file.name)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        stdout_redirect = contextlib.redirect_stdout(self.stdout)
        stderr_redirect = contextlib.redirect_stderr(self.stderr)
        stdout_redirect.__enter__()
        stderr_redirect.__enter__()
        self.addCleanup(stderr_redirect.__exit__, None, None, None)
        self.addCleanup(stdout_redirect.__exit__, None, None, None)

    def test_git_status_uses_precomputed_repository_roots(self):
        completed = argparse.Namespace(returncode=0)
        with mock.patch.object(sync_modules, "run_command", return_value=completed) as run:
            self.assertEqual(
                sync_modules.run_git_operation("git-status", ["/repo/one"]),
                0,
            )
        run.assert_called_once_with([
            "git", "-C", "/repo/one", "status", "--short", "--branch",
            "--untracked-files=no",
        ], check=False)

    def test_git_status_propagates_failure(self):
        completed = argparse.Namespace(returncode=1)
        with mock.patch.object(sync_modules, "run_command", return_value=completed):
            self.assertEqual(sync_modules.run_git_operation("git-status", ["/repo"]), 1)

    def test_unknown_git_operation_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported Git operation"):
            sync_modules.run_git_operation("unknown", [])

    def test_main_passes_scanned_projects_directly_to_git_operation(self):
        options = argparse.Namespace(
            config="/config", workspace=None, command="pull", verbose=0
        )
        config = {
            "workspace": "/workspace", "jobs": 1,
            "extensions": ["py"], "exclude": ["old"],
        }
        with (
            mock.patch.object(sync_modules, "load_config", return_value=config),
            mock.patch.object(sync_modules, "parse_arguments", return_value=options),
            mock.patch.object(sync_modules, "get_projects", return_value=["one", "two"]) as discover,
            mock.patch.object(sync_modules, "get_repository_roots", return_value=["/repo/one", "/repo/two"]) as roots,
            mock.patch.object(sync_modules, "run_git_operation", return_value=0) as git_operation,
        ):
            self.assertEqual(sync_modules.main(), 0)
        discover.assert_called_once_with(Path("/workspace"), ["py"], ["old"])
        roots.assert_called_once_with([Path("/workspace/one"), Path("/workspace/two")])
        git_operation.assert_called_once_with("pull", ["/repo/one", "/repo/two"])

    def test_parse_github_remote_and_rewrite_project(self):
        remote = sync_modules.parse_github_remote("git@github.com:owner/source.git")
        self.assertEqual(remote.owner, "owner")
        self.assertEqual(
            remote.url_for_project("target"),
            "git@github.com:owner/target.git",
        )

    def test_clone_skips_existing_project(self):
        remote = sync_modules.Remote(
            "https://github.com/owner/source.git", "owner", "source"
        )
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "target").mkdir()
            with mock.patch.object(sync_modules, "run_command") as run:
                sync_modules.clone_project(remote, "target", workspace)
            run.assert_not_called()

    def test_clone_rejects_path_as_project_name(self):
        remote = sync_modules.Remote(
            "https://github.com/owner/source.git", "owner", "source"
        )
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(RuntimeError, "must not contain '/'"):
                sync_modules.clone_project(remote, "../target", workspace)

    def test_clone_rejects_project_combined_with_list(self):
        options = argparse.Namespace(
            config="/config", workspace=None, command="clone", verbose=0,
            project="target", list=True, all=False,
        )
        config = {
            "workspace": "/workspace", "jobs": 1,
            "extensions": ["py"], "exclude": [],
        }
        with (
            mock.patch.object(sync_modules, "load_config", return_value=config),
            mock.patch.object(sync_modules, "parse_arguments", return_value=options),
            mock.patch.object(sync_modules, "run_clone_command") as clone,
        ):
            self.assertEqual(sync_modules.main(), 1)
        clone.assert_not_called()

    def test_project_list_includes_local_git_state(self):
        clean = argparse.Namespace(returncode=0, stdout="## main...origin/main\n")
        modified = argparse.Namespace(
            returncode=0, stdout="## topic...origin/topic [ahead 1]\n M file.py\n"
        )
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "clean", ".git").mkdir(parents=True)
            Path(workspace, "modified", ".git").mkdir(parents=True)

            def status(args, **kwargs):
                return modified if Path(args[2]).name == "modified" else clean

            with mock.patch.object(sync_modules, "run_command", side_effect=status):
                sync_modules.print_project_list(
                    ["clean", "modified", "remote-only"], workspace
                )
        output = self.stdout.getvalue()
        self.assertIn("REPOSITORY", output)
        self.assertRegex(output, r"clean\s+yes\s+main\.\.\.origin/main")
        self.assertRegex(
            output,
            r"modified\s+yes\s+topic\.\.\.origin/topic \[ahead 1\]; modified",
        )
        self.assertRegex(output, r"remote-only\s+no\s+-")

    def test_local_project_info_ignores_untracked_files(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "-q", directory], check=True)
            tracked = Path(directory, "tracked.txt")
            tracked.write_text("original\n")
            subprocess.run(["git", "-C", directory, "add", "tracked.txt"], check=True)
            subprocess.run(
                ["git", "-C", directory, "-c", "user.name=Test", "-c",
                 "user.email=test@example.invalid", "commit", "-qm", "initial"],
                check=True,
            )
            Path(directory, "untracked.txt").write_text("new\n")
            local, info = sync_modules.get_local_project_info(directory)
            self.assertEqual(local, "yes")
            self.assertNotIn("modified", info)

            tracked.write_text("changed\n")
            _, info = sync_modules.get_local_project_info(directory)
            self.assertIn("modified", info)

    def test_list_command_uses_same_remote_listing_as_clone_list(self):
        remote = sync_modules.Remote(
            "https://github.com/owner/source.git", "owner", "source"
        )
        with (
            mock.patch.object(sync_modules, "find_remote", return_value=remote),
            mock.patch.object(
                sync_modules, "list_remote_projects", return_value=["one"]
            ) as list_remote,
            mock.patch.object(sync_modules, "print_project_list") as print_list,
        ):
            sync_modules.run_list_command("/workspace")
        list_remote.assert_called_once_with(remote)
        print_list.assert_called_once_with(["one"], Path("/workspace"))

    def test_new_dry_run_configures_upstream(self):
        options = argparse.Namespace(
            project="owner/new_repo", dry_run=True, verbose=0,
            list_existing_projects=False, limit=30, owner=None,
            visibility="--private", description=None, homepage=None,
            gitignore=None, license=None, add_readme=False,
            disable_issues=False, disable_wiki=False, remote=None,
            team=None, template=None,
        )
        with tempfile.TemporaryDirectory() as workspace:
            with mock.patch.object(sync_modules, "run_command") as run:
                sync_modules.run_new_command(options, workspace)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(
                run.call_args_list[0].args[0],
                ["gh", "repo", "create", "owner/new_repo", "--private", "--clone"],
            )

    def test_command_formatting_accepts_path_objects(self):
        sync_modules.print_command(["git", "status"], cwd=Path("/workspace"))
        self.assertEqual(self.stdout.getvalue(), "(cd /workspace && git status)\n")

    def test_project_command_propagates_failure(self):
        completed = argparse.Namespace(returncode=2)
        with mock.patch.object(sync_modules, "run_command", return_value=completed) as run:
            result = sync_modules.run_project_commands("clean", ["one"], "/workspace", 3)
        self.assertEqual(result, 1)
        run.assert_called_once_with(
            ["make", "-j", "3", "-C", "/workspace/one", "clean"],
            cwd="/workspace", check=False,
        )

    def test_parallel_make_is_only_used_for_build(self):
        with mock.patch.object(
            sync_modules.concurrent.futures, "ThreadPoolExecutor"
        ) as executor:
            executor.return_value.__enter__.return_value.map.return_value = []
            sync_modules.run_project_commands("unit-test", ["one", "two"], "/workspace", 8)
        executor.assert_called_once_with(max_workers=1)

    def test_project_discovery_supports_all_extensions_and_exclusions(self):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "cpp", "src").mkdir(parents=True)
            Path(workspace, "cpp", "src", "module.cpp").touch()
            Path(workspace, "python").mkdir()
            Path(workspace, "python", "module.py").touch()
            Path(workspace, "ignored").mkdir()
            Path(workspace, "ignored", "notes.txt").touch()
            Path(workspace, "old").mkdir()
            Path(workspace, "old", "module.py").touch()
            self.assertEqual(
                sync_modules.get_projects(workspace, ["cpp", "py"], ["old"]),
                ["cpp", "python"],
            )

    def test_variant_analysis_selects_newest_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            older_path = Path(directory, "one", "module.py")
            newer_path = Path(directory, "two", "module.py")
            older_path.parent.mkdir()
            newer_path.parent.mkdir()
            older_path.write_text("old")
            newer_path.write_text("new")
            os.utime(older_path, (1, 1))
            os.utime(newer_path, (2, 2))
            files = [
                sync_modules.SharedFile.load(older_path),
                sync_modules.SharedFile.load(newer_path),
            ]
            in_sync, variants = sync_modules.find_variants(files)
        self.assertEqual(in_sync, [])
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0].newest.path, newer_path)

    def test_diff_does_not_check_git_cleanliness(self):
        with tempfile.TemporaryDirectory() as directory:
            for project, content in (("one", "old"), ("two", "new")):
                path = Path(directory, project, "module.py")
                path.parent.mkdir()
                path.write_text(content)
            options = argparse.Namespace(
                project_paths=[Path(directory, "one"), Path(directory, "two")],
                command="diff", extensions=["py"], filter="", exclude=[],
                no_git_check=False, verbose=0,
            )
            with mock.patch.object(sync_modules, "check_git_clean_for_file") as check:
                self.assertEqual(sync_modules.run_sync_operation(options), 0)
            check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
