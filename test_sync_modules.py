import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name("sync_modules.py")
SPEC = importlib.util.spec_from_file_location("sync_modules", SCRIPT)
sync_modules = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_modules)


class ConfigTests(unittest.TestCase):
    def test_default_config_is_valid(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".toml") as config_file:
            config_file.write(
                'workspace = "github"\njobs = 0\n'
                'extensions = ["c", "py"]\n'
                'exclude = ["old", "keep"]\n'
            )
            config_file.flush()
            config = sync_modules.loadConfig(config_file.name)
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
                sync_modules.loadConfig(config_file.name)


class CommandTests(unittest.TestCase):
    def test_git_status_uses_precomputed_repository_roots(self):
        completed = argparse.Namespace(returncode=0)
        with mock.patch.object(sync_modules.subprocess, "run", return_value=completed) as run:
            self.assertEqual(
                sync_modules.runGitOperation("git-status", ["/repo/one"]),
                0,
            )
        run.assert_called_once_with([
            "git", "-C", "/repo/one", "status", "--short", "--branch",
            "--untracked-files=no",
        ])

    def test_git_status_propagates_failure(self):
        completed = argparse.Namespace(returncode=1)
        with mock.patch.object(sync_modules.subprocess, "run", return_value=completed):
            self.assertEqual(sync_modules.runGitOperation("git-status", ["/repo"]), 1)

    def test_unknown_git_operation_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Unsupported Git operation"):
            sync_modules.runGitOperation("unknown", [])

    def test_main_passes_scanned_projects_directly_to_git_operation(self):
        options = argparse.Namespace(
            config="/config", workspace=None, command="pull", verbose=0
        )
        config = {
            "workspace": "/workspace", "jobs": 1,
            "extensions": ["py"], "exclude": ["old"],
        }
        with (
            mock.patch.object(sync_modules, "loadConfig", return_value=config),
            mock.patch.object(sync_modules, "parseArguments", return_value=options),
            mock.patch.object(sync_modules, "getProjects", return_value=["one", "two"]) as discover,
            mock.patch.object(sync_modules, "getRepositoryRoots", return_value=["/repo/one", "/repo/two"]) as roots,
            mock.patch.object(sync_modules, "runGitOperation", return_value=0) as git_operation,
        ):
            self.assertEqual(sync_modules.main(), 0)
        discover.assert_called_once_with("/workspace", ["py"], ["old"])
        roots.assert_called_once_with(["/workspace/one", "/workspace/two"])
        git_operation.assert_called_once_with("pull", ["/repo/one", "/repo/two"])

    def test_parse_github_remote_and_rewrite_project(self):
        remote = sync_modules.parseGithubRemote("git@github.com:owner/source.git")
        self.assertEqual(remote.owner, "owner")
        self.assertEqual(
            remote.urlForProject("target"),
            "git@github.com:owner/target.git",
        )

    def test_clone_skips_existing_project(self):
        remote = sync_modules.Remote(
            "https://github.com/owner/source.git", "github.com", "owner", "source"
        )
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "target").mkdir()
            with mock.patch.object(sync_modules, "runCommand") as run:
                sync_modules.cloneProject(remote, "target", workspace)
            run.assert_not_called()

    def test_clone_rejects_path_as_project_name(self):
        remote = sync_modules.Remote(
            "https://github.com/owner/source.git", "github.com", "owner", "source"
        )
        with tempfile.TemporaryDirectory() as workspace:
            with self.assertRaisesRegex(RuntimeError, "must not contain '/'"):
                sync_modules.cloneProject(remote, "../target", workspace)

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
            mock.patch.object(sync_modules, "loadConfig", return_value=config),
            mock.patch.object(sync_modules, "parseArguments", return_value=options),
            mock.patch.object(sync_modules, "runCloneCommand") as clone,
        ):
            self.assertEqual(sync_modules.main(), 1)
        clone.assert_not_called()

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
            with mock.patch.object(sync_modules, "runCommand") as run:
                sync_modules.runNewCommand(options, workspace)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(
                run.call_args_list[0].args[0],
                ["gh", "repo", "create", "owner/new_repo", "--private", "--clone"],
            )

    def test_project_command_propagates_failure(self):
        completed = argparse.Namespace(returncode=2)
        with mock.patch.object(sync_modules.subprocess, "run", return_value=completed) as run:
            result = sync_modules.runProjectCommands("clean", ["one"], "/workspace", 3)
        self.assertEqual(result, 1)
        run.assert_called_once_with(
            ["make", "-j", "3", "-C", "/workspace/one", "clean"], cwd="/workspace"
        )

    def test_parallel_make_is_only_used_for_build(self):
        with mock.patch.object(
            sync_modules.concurrent.futures, "ThreadPoolExecutor"
        ) as executor:
            executor.return_value.__enter__.return_value.map.return_value = []
            sync_modules.runProjectCommands("unit-test", ["one", "two"], "/workspace", 8)
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
                sync_modules.getProjects(workspace, ["cpp", "py"], ["old"]),
                ["cpp", "python"],
            )


if __name__ == "__main__":
    unittest.main()
