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
    def test_project_command_propagates_failure(self):
        completed = argparse.Namespace(returncode=2)
        with mock.patch.object(sync_modules.subprocess, "run", return_value=completed) as run:
            result = sync_modules.runProjectCommands("clean", ["one"], "/workspace", 3)
        self.assertEqual(result, 1)
        run.assert_called_once_with(
            ["make", "-j", "3", "-C", "/workspace/one", "clean"], cwd="/workspace"
        )

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
