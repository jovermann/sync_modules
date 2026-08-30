#!/usr/bin/python3
#
# sync_modules.py - synchronize shared source modules and maintain their projects
#
# Copyright (C) 2024-2025 by Johannes Overmann <Johannes.Overmann@joov.de>
#
# Distributed under the Boost Software License, Version 1.0.
# (See accompanying file LICENSE or copy at https://www.boost.org/LICENSE_1_0.txt)

import argparse
import concurrent.futures
import os
import re
import datetime
import hashlib
import importlib.util
import difflib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path.home() / ".sync_modules.toml"

# Load the local copy explicitly, avoiding a similarly named third-party
# package from the Python environment. sync_modules.py keeps this copy in sync
# with the other shared-module copies.
TOML_PATH = SCRIPT_DIR / "toml.py"
TOML_SPEC = importlib.util.spec_from_file_location("netview_toml", TOML_PATH)
if TOML_SPEC is None or TOML_SPEC.loader is None:
    raise SystemExit(f"Cannot load shared TOML module at {TOML_PATH}")
toml = importlib.util.module_from_spec(TOML_SPEC)
try:
    TOML_SPEC.loader.exec_module(toml)
except FileNotFoundError as exc:
    raise SystemExit(f"Cannot find shared TOML module at {TOML_PATH}") from exc

extensions = ""
filter = ""
exclude = []
defaultWorkspace = str(SCRIPT_DIR.parent)

class File:
    """Path, basename and content of an existing file in the filesystem.
    """

    def __init__(self, path):
        """Store path, basename and file content of an existing file.
        """
        self.path = path
        self.basename = os.path.basename(path)
        self.content = ""
        self.hash = ""
        with open(path, "rb") as file:
            self.content = file.read()
            self.hash = hashlib.sha256(self.content).hexdigest()


def addFile(files, path):
    """Add file to files if it has an accepted extension.
    """
    ext = os.path.splitext(path)[1][1:]
    if ext not in extensions:
        return
    basename = os.path.basename(path)
    if filter:
        if not re.fullmatch(filter, basename):
            return
    files.append(File(path))


def addDir(files, path):
    """Add all files in dir, recursively.
    """
    for walkpath, walkdirs, walkfiles in os.walk(path):
        walkdirs[:] = [d for d in walkdirs if d not in exclude]
        for f in walkfiles:
            if f in exclude:
                continue
            addFile(files, os.path.join(walkpath, f))


def printDiff(file_a, file_b):
    """Print diff.
    """
    a_text = file_a.content.decode("utf-8", errors="replace").splitlines(keepends=True)
    b_text = file_b.content.decode("utf-8", errors="replace").splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_text,
        b_text,
        fromfile=file_a.path,
        tofile=file_b.path,
    )
    for line in diff:
        sys.stdout.write(line)


def copyFile(fromPath, toPath):
    """Copy file.
    """
    print(f"Copying {fromPath.path} -> {toPath.path}")
    shutil.copy2(fromPath.path, toPath.path)


def getNewestAndOther(map):
    """Return a tuple (newestFile, listOfOtherFiles).
    """
    newest = None
    newest_mtime = None
    other = []
    for files in map.values():
        for file in files:
            mtime = os.path.getmtime(file.path)
            if newest is None or mtime > newest_mtime:
                if newest is not None:
                    other.append(newest)
                newest = file
                newest_mtime = mtime
            else:
                other.append(file)
    return (newest, other)


def getSmallestFile(map):
    """Get smallest file in map.
    """
    return map[min(map, key=len)]

def getUniqueHashPrefixes(files, min_len=4):
    """Return a map of full hash to smallest unique prefix.
    """
    hashes = sorted(set(f.hash for f in files))
    prefix_map = {}
    for h in hashes:
        for length in range(min_len, len(h) + 1):
            prefix = h[:length]
            if sum(1 for other in hashes if other.startswith(prefix)) == 1:
                prefix_map[h] = prefix
                break
        else:
            prefix_map[h] = h
    return prefix_map

def getRepoRootForFile(file):
    """Return git repo root for file, or None if not in a repo.
    """
    repo_dir = os.path.dirname(file.path)
    result = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()

def getRepoRoots(variant_sets):
    """Return sorted unique repo roots for variant sets.
    """
    roots = set()
    for entry in variant_sets:
        for files in entry["hash_to_files"].values():
            for file in files:
                root = getRepoRootForFile(file)
                if root:
                    roots.add(root)
    return sorted(roots)

def getRepoRootsFromFiles(files):
    """Return sorted unique repo roots for a list of files.
    """
    roots = set()
    for file in files:
        root = getRepoRootForFile(file)
        if root:
            roots.add(root)
    return sorted(roots)

def getRepoRootsFromPaths(paths):
    """Return sorted unique repo roots containing the supplied paths."""
    roots = set()
    for path in paths:
        probe = path if os.path.isdir(path) else os.path.dirname(path)
        result = subprocess.run(
            ["git", "-C", probe, "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if result.returncode == 0:
            roots.add(result.stdout.strip())
    return sorted(roots)

def repoHasModifications(repo_root):
    """Return True if repo has tracked changes (staged or unstaged), ignoring untracked files."""
    status = subprocess.run(
        ["git", "-C", repo_root, "status", "--porcelain", "--untracked-files=no"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if status.returncode != 0:
        return False
    return bool(status.stdout.strip())

def getLastCommitMessage(repo_root):
    """Return the last commit message for the repo.
    """
    result = subprocess.run(
        ["git", "-C", repo_root, "log", "-1", "--pretty=%B"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()

def checkGitCleanForFile(file):
    """Check whether the file itself is clean in its git repo.
    """
    repo_dir = os.path.dirname(file.path)
    result = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        return (False, f"'{repo_dir}' is not in a git repo.")
    repo_root = result.stdout.strip()
    relpath = os.path.relpath(file.path, repo_root)
    status = subprocess.run(
        [
            "git",
            "-C",
            repo_root,
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            relpath,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if status.returncode != 0:
        return (False, f"Failed to check git status in '{repo_root}'.")
    if status.stdout.strip():
        return (False, f"File '{file.path}' has local changes.")
    return (True, "")

def getProjects(workspace, accepted_extensions, excluded_names):
    """Return immediate child projects containing accepted source files."""
    projects = []
    accepted_extensions = {extension.lstrip(".") for extension in accepted_extensions}
    ignored = {".git", ".svn", "__pycache__", "build", *excluded_names}
    try:
        children = sorted(os.scandir(workspace), key=lambda entry: entry.name)
    except OSError as e:
        raise RuntimeError(f"Cannot scan workspace '{workspace}': {e}")
    for child in children:
        if (
            not child.is_dir(follow_symlinks=True)
            or child.name.startswith(".")
            or child.name in ignored
        ):
            continue
        found = False
        for _, dirs, files in os.walk(child.path):
            dirs[:] = [name for name in dirs if name not in ignored and not name.startswith(".")]
            if any(
                name not in ignored
                and os.path.splitext(name)[1].lstrip(".") in accepted_extensions
                for name in files
            ):
                found = True
                break
        if found:
            projects.append(child.name)
    return projects


def runSyncOperation(options):
    """Compare, synchronize, or run a Git operation on selected paths."""
    global extensions, filter, exclude
    if options.status:
        options.show_sync_sources = True
        options.git_status = True

    extensions = options.extensions
    filter = options.filter
    exclude = options.exclude

    try:
        # Read all files and dirs.
        fileListAll = []
        for path in options.args:
            if not os.path.exists(path):
                print("Error: Path '{}' does not exist.\n".format(path))
            elif os.path.isfile(path):
                # Explicit files are intentional synchronization inputs and do
                # not need to match the directory extension filter.
                fileListAll.append(File(path))
            elif os.path.isdir(path):
                addDir(fileListAll, path)
            else:
                print("Warning: Ignoring non-regular file '{}'.\n".format(path))

        # Build basename to file list map.
        name2fileList = {}
        for f in fileListAll:
            if f.basename not in name2fileList:
                name2fileList[f.basename] = [f]
            else:
                name2fileList[f.basename].append(f)

        # Build file set variants.
        in_sync_names = []
        variant_sets = []
        involved_files = []
        for name, fileList in sorted(name2fileList.items()):
            involved_files.extend(fileList)
            if len(fileList) < 2:
                continue

            # Build hash to file list map.
            hashToFiles = {}
            for file in fileList:
                if file.content not in hashToFiles:
                    hashToFiles[file.content] = [file]
                else:
                    hashToFiles[file.content].append(file)

            if len(hashToFiles) == 1:
                in_sync_names.append((name, len(fileList)))
                continue

            newest, other = getNewestAndOther(hashToFiles)
            variant_sets.append(
                {
                    "name": name,
                    "hash_to_files": hashToFiles,
                    "newest": newest,
                    "other": other,
                }
            )

        for name, count in in_sync_names:
            print(f"(File {name} is in sync across {count} files.)")

        for entry in variant_sets:
            all_files = []
            for files in entry["hash_to_files"].values():
                all_files.extend(files)
            hash_prefixes = getUniqueHashPrefixes(all_files)
            print(f"File {entry['name']} exists in {len(entry['hash_to_files'])} variants:")
            for hash in sorted(entry["hash_to_files"], key=len):
                files = entry["hash_to_files"][hash]
                for file in files:
                    mtime = os.path.getmtime(file.path)
                    date = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    sync_source = " (sync-source)" if file.path == entry["newest"].path else ""
                    print(f"    hash={hash_prefixes[file.hash]} len={len(file.content):6d} date={date} {file.path}{sync_source}")

        if options.show_sync_sources and not variant_sets:
            print("All shared files are in sync; sync_modules.py sync would copy nothing.")

        if options.diff:
            for entry in variant_sets:
                content_to_files = {}
                for file in entry["other"]:
                    if file.content not in content_to_files:
                        content_to_files[file.content] = [file]
                    else:
                        content_to_files[file.content].append(file)
                for files in content_to_files.values():
                    for file in files:
                        print(f"diff -u {file.path} {entry['newest'].path}")
                    printDiff(files[0], entry["newest"])

        if options.git_diff:
            repo_roots = sorted(set(getRepoRootsFromFiles(involved_files) + getRepoRootsFromPaths(options.args)))
            for repo_root in repo_roots:
                print(f"Running git diff in {repo_root}")
                subprocess.run(["git", "-C", repo_root, "--no-pager", "diff"])

        if options.git_status:
            repo_roots = sorted(set(getRepoRootsFromFiles(involved_files) + getRepoRootsFromPaths(options.args)))
            for repo_root in repo_roots:
                print(f"Running git status in {repo_root}")
                subprocess.run(
                    [
                        "git",
                        "-C",
                        repo_root,
                        "status",
                        "--short",
                        "--branch",
                        "--untracked-files=no",
                    ]
                )

        if options.pull or options.push or options.commit:
            repo_roots = sorted(set(getRepoRootsFromFiles(involved_files) + getRepoRootsFromPaths(options.args)))
            commit_message_file = None
            commit_message = ""
            first_commit_done = False
            try:
                for repo_root in repo_roots:
                    committed_this_repo = False
                    if options.pull:
                        print(f"Running git pull --rebase in {repo_root}")
                        subprocess.run(["git", "-C", repo_root, "pull", "--rebase"])
                    if options.commit:
                        if not repoHasModifications(repo_root):
                            print(f"Skipping commit in {repo_root} (no modifications)")
                        else:
                            if not first_commit_done:
                                print(f"Running git commit -a in {repo_root}")
                                result = subprocess.run(["git", "-C", repo_root, "commit", "-a"])
                                if result.returncode != 0:
                                    print(f"Error: Commit failed in {repo_root}.")
                                    sys.exit(1)
                                commit_message = getLastCommitMessage(repo_root)
                                if not commit_message:
                                    print("Warning: Empty commit message, skipping commits.")
                                    break
                                with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp:
                                    tmp.write(commit_message)
                                    commit_message_file = tmp.name
                                first_commit_done = True
                            else:
                                print(f"Running git commit -a in {repo_root}")
                                subprocess.run(["git", "-C", repo_root, "commit", "-a", "-F", commit_message_file])
                            committed_this_repo = True
                    if options.push:
                        if options.commit and not committed_this_repo:
                            continue
                        print(f"Running git push in {repo_root}")
                        subprocess.run(["git", "-C", repo_root, "push"])
            finally:
                if commit_message_file:
                    os.unlink(commit_message_file)

        blocked_paths = set()
        git_error = False
        if not options.no_git_check:
            for entry in variant_sets:
                for file in entry["other"]:
                    ok, message = checkGitCleanForFile(file)
                    if options.verbose:
                        status = "clean" if ok else "modified"
                        print(f"Git status for {file.path}: {status}")
                    if not ok:
                        if options.sync:
                            print(f"Error: {message}")
                            blocked_paths.add(file.path)
                            git_error = True
                        else:
                            print(f"Warning: {message}")

        if options.sync and git_error:
            print("Error: Aborting --sync due to git check failures.")
            sys.exit(1)

        if options.sync:
            for entry in variant_sets:
                for file in entry["other"]:
                    if file.path in blocked_paths:
                        continue
                    copyFile(entry["newest"], file)


    except RuntimeError as e:
        print("Error: {}".format(str(e)))
        return 1
    return 0


def loadConfig(path):
    """Load and validate persistent command defaults."""
    try:
        with open(path, "rb") as config_file:
            config = toml.load(config_file)
    except (OSError, toml.TOMLDecodeError) as exc:
        raise RuntimeError(f"Cannot load config '{path}': {exc}") from exc
    required_lists = (
        "extensions", "exclude",
    )
    for key in required_lists:
        if not isinstance(config.get(key), list) or not all(isinstance(item, str) for item in config[key]):
            raise RuntimeError(f"Config option '{key}' must be an array of strings")
    if not isinstance(config.get("workspace"), str):
        raise RuntimeError("Config option 'workspace' must be a string")
    if not isinstance(config.get("jobs"), int) or config["jobs"] < 0:
        raise RuntimeError("Config option 'jobs' must be a non-negative integer")
    return config


def runProjectCommands(command, projects, workspace, jobs):
    """Run a maintenance command for each selected project."""
    if command == "clone-all":
        commands = [([str(SCRIPT_DIR / "git_clone.py"), "-C", workspace, project], workspace) for project in projects]
    else:
        target = {"build": None, "unit-test": "unit_test", "clean": "clean"}[command]
        commands = []
        for project in projects:
            argv = ["make"]
            if command in ("unit-test", "clean"):
                argv += ["-j", str(jobs)]
            argv += ["-C", os.path.join(workspace, project)]
            if target:
                argv.append(target)
            commands.append((argv, workspace))

    def run(entry):
        argv, cwd = entry
        print("Running " + " ".join(argv), flush=True)
        return subprocess.run(argv, cwd=cwd).returncode

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        return 1 if any(pool.map(run, commands)) else 0


def parseArguments(config):
    parser = argparse.ArgumentParser(description="Synchronize shared modules and maintain their projects.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="TOML config file (default: %(default)s).")
    parser.add_argument("--workspace", help="Override the workspace from the config file.")
    parser.add_argument("-V", "--verbose", action="count", default=0, help="Increase verbosity.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    help_text = {
        "diff": "Show differences between synchronized module copies.",
        "sync": "Copy newest module versions to matching older copies.",
        "commit": "Commit changes in affected repositories.",
        "pull": "Pull affected repositories with rebase.",
        "push": "Push affected repositories.",
        "status": "Show sync sources and repository status.",
        "git-diff": "Show diffs in affected repositories.",
        "clone-all": "Clone all missing project repositories.",
        "build": "Build all relevant repositories.",
        "unit-test": "Build and run all relevant unit tests.",
        "clean": "Clean all relevant repositories.",
    }
    for name, description in help_text.items():
        command_parser = subparsers.add_parser(name, help=description, description=description)
        if name in ("diff", "sync", "commit", "pull", "push", "status", "git-diff"):
            command_parser.add_argument("paths", nargs="*", help="Override the automatically discovered project paths.")
            command_parser.add_argument("-f", "--filter", default="", help="Only process filenames matching this regex.")
        if name == "sync":
            command_parser.add_argument("--no-git-check", action="store_true", help="Allow overwriting locally modified files.")
    return parser.parse_args()


def main():
    # Parse --config first because it supplies defaults used by all commands.
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--config", default=str(DEFAULT_CONFIG))
    bootstrap_options, _ = bootstrap.parse_known_args()
    try:
        config = loadConfig(bootstrap_options.config)
    except RuntimeError as exc:
        bootstrap.error(str(exc))
    options = parseArguments(config)
    workspace = options.workspace or config["workspace"]
    if not os.path.isabs(workspace):
        workspace = os.path.abspath(os.path.join(os.path.dirname(options.config), workspace))
    jobs = config["jobs"] or (os.cpu_count() or 1)

    try:
        projects = getProjects(workspace, config["extensions"], config["exclude"])
        if options.command in ("clone-all", "build", "unit-test", "clean"):
            if options.command != "clone-all":
                projects = [
                    project for project in projects
                    if os.path.isfile(os.path.join(workspace, project, "Makefile"))
                ]
            return runProjectCommands(options.command, projects, workspace, jobs)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    paths = options.paths or [os.path.join(workspace, project) for project in projects]
    operation = argparse.Namespace(
        args=paths,
        extensions=config["extensions"],
        filter=options.filter,
        exclude=config["exclude"],
        diff=options.command == "diff",
        sync=options.command == "sync",
        pull=options.command == "pull",
        push=options.command == "push",
        commit=options.command == "commit",
        git_diff=options.command == "git-diff",
        git_status=options.command == "status",
        status=options.command == "status",
        show_sync_sources=options.command == "status",
        no_git_check=getattr(options, "no_git_check", False) or options.command == "status",
        verbose=options.verbose,
    )
    return runSyncOperation(operation)



# Call main().
if __name__ == "__main__":
    sys.exit(main())
