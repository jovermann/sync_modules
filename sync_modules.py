#!/usr/bin/python3
#
# sync_modules.py - synchronize shared source modules and maintain their projects
#
# Copyright (C) 2024-2026 by Johannes Overmann <Johannes.Overmann@joov.de>
#
# Distributed under the Boost Software License, Version 1.0.
# (See accompanying file LICENSE or copy at https://www.boost.org/LICENSE_1_0.txt)

import argparse
import concurrent.futures
import os
import re
import shlex
import datetime
import hashlib
import importlib.util
import difflib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = Path.home() / ".sync_modules.toml"
DEFAULT_CONFIG_CONTENT = '''workspace = "github"
jobs = 0

extensions = ["c", "h", "cpp", "hpp", "cxx", "hxx", "py", "sh"]

exclude = [
    "streplace_0.9",
    "old",
    "other",
    "test_basic.py",
    "keep",
    ".venv",
]
'''

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

class Remote:
    """Parsed GitHub remote URL."""

    def __init__(self, url, owner, repo):
        self.url = url
        self.owner = owner
        self.repo = repo

    def url_for_project(self, project):
        """Return a remote URL like this one with a different repository name."""
        suffixes = [
            ("/" + self.repo + ".git", "/" + project + ".git"),
            ("/" + self.repo, "/" + project),
            (":" + self.owner + "/" + self.repo + ".git", ":" + self.owner + "/" + project + ".git"),
            (":" + self.owner + "/" + self.repo, ":" + self.owner + "/" + project),
        ]
        for suffix, replacement in suffixes:
            if self.url.endswith(suffix):
                return self.url[: -len(suffix)] + replacement
        raise RuntimeError(f"Cannot rewrite remote URL '{self.url}'.")


def print_command(args, cwd=None):
    """Print a shell-style representation of a command."""
    prefix = f"(cd {shlex.quote(str(cwd))} && " if cwd else "("
    print(prefix + " ".join(shlex.quote(str(arg)) for arg in args) + ")")


def run_command(args, cwd=None, capture=False, check=True, dry_run=False):
    """Run a command, or print it in dry-run mode."""
    if dry_run:
        print_command(args, cwd=cwd)
        return None
    kwargs = {}
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = subprocess.run(args, cwd=cwd, **kwargs)
    if check and result.returncode != 0:
        command = " ".join(map(str, args))
        if capture and result.stderr:
            raise RuntimeError(f"Command failed: {command}\n{result.stderr.strip()}")
        raise RuntimeError(f"Command failed: {command}")
    return result


def parse_github_remote(url):
    """Parse common GitHub remote URL forms."""
    patterns = [
        r"^https://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return Remote(url, match.group(2), match.group(3))
    return None


def get_remote_url(repo_dir):
    """Return a likely fetch remote URL from a repository, or None."""
    remote_names = ["origin"]
    remotes = run_command(["git", "-C", repo_dir, "remote"], capture=True, check=False)
    if remotes.returncode == 0:
        remote_names.extend(name for name in remotes.stdout.splitlines() if name and name != "origin")
    for remote_name in remote_names:
        result = run_command(
            ["git", "-C", repo_dir, "remote", "get-url", remote_name],
            capture=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def find_remote(start_dir):
    """Find and parse a GitHub remote in a directory or direct child."""
    start_dir = Path(start_dir)
    candidates = []
    if (start_dir / ".git").is_dir():
        candidates.append(start_dir)
    try:
        entries = sorted(start_dir.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise RuntimeError(f"Cannot inspect '{start_dir}': {exc}") from exc
    for path in entries:
        if (path / ".git").is_dir():
            candidates.append(path)
    for repo_dir in candidates:
        url = get_remote_url(repo_dir)
        remote = parse_github_remote(url) if url else None
        if remote:
            return remote
    return None


def list_remote_projects(remote):
    """List repositories owned by the inferred GitHub owner."""
    if shutil.which("gh") is None:
        raise RuntimeError("Cannot list projects because 'gh' is not installed.")
    result = run_command(
        ["gh", "repo", "list", remote.owner, "--limit", "1000", "--json", "name", "--jq", ".[].name"],
        capture=True,
    )
    return sorted(name for name in result.stdout.splitlines() if name)


def clone_project(remote, project, work_dir):
    """Clone one project unless its target already exists."""
    validate_project_name(project, allow_owner=False)
    target = Path(work_dir) / project
    if target.exists():
        print(f"Skipping {project} (already exists)")
        return
    url = remote.url_for_project(project)
    print(f"Cloning {url}")
    run_command(["git", "clone", url, project], cwd=work_dir)


def run_clone_command(options, default_workspace):
    """Implement the clone subcommand."""
    work_dir = Path(default_workspace).resolve()
    remote = find_remote(work_dir)
    if remote is None:
        raise RuntimeError(f"Found no GitHub remote in '{work_dir}' or its direct subdirectories.")
    projects = list_remote_projects(remote) if options.list or options.all else []
    if options.list:
        for project in projects:
            print(project)
    if options.all:
        for project in projects:
            clone_project(remote, project, work_dir)
    if options.project:
        clone_project(remote, options.project, work_dir)


def validate_project_name(project, allow_owner=True):
    """Reject values which look like paths rather than repository names."""
    if project in ("", ".", ".."):
        raise RuntimeError("Repository name must not be empty, '.', or '..'.")
    if project.startswith("/") or project.endswith("/"):
        raise RuntimeError("Repository name must not start or end with '/'.")
    if "\\" in project:
        raise RuntimeError("Repository name must not contain backslashes.")
    if not allow_owner and "/" in project:
        raise RuntimeError("Repository name must not contain '/'.")
    if project.count("/") > 1:
        raise RuntimeError("Use REPO or OWNER/REPO, not a path.")
    if any(part in ("", ".", "..") for part in project.split("/")):
        raise RuntimeError("Repository owner and name must not be empty, '.', or '..'.")


def configure_upstream(clone_dir, remote_name, dry_run=False):
    """Configure an initial branch to track its same-named remote branch."""
    branch = "main"
    if not dry_run:
        result = run_command(["git", "-C", clone_dir, "symbolic-ref", "--short", "HEAD"], capture=True)
        branch = result.stdout.strip()
        if not branch:
            raise RuntimeError(f"Cannot determine the initial branch in '{clone_dir}'.")
    run_command(["git", "config", f"branch.{branch}.remote", remote_name], cwd=clone_dir, dry_run=dry_run)
    run_command(["git", "config", f"branch.{branch}.merge", f"refs/heads/{branch}"], cwd=clone_dir, dry_run=dry_run)


def run_new_command(options, default_workspace):
    """Create or search for a GitHub repository."""
    work_dir = Path(default_workspace).resolve()
    validate_project_name(options.project)
    if not work_dir.is_dir():
        raise RuntimeError(f"Workspace directory does not exist: '{work_dir}'.")
    if options.list_existing_projects:
        if options.limit <= 0:
            raise RuntimeError("--limit must be greater than zero.")
        if shutil.which("gh") is None and not options.dry_run:
            raise RuntimeError("Cannot search repositories because 'gh' is not installed.")
        run_command(
            ["gh", "search", "repos", "--limit", str(options.limit), "--", options.project],
            dry_run=options.dry_run,
        )
        return

    owner = options.owner
    if not owner and "/" not in options.project:
        remote = find_remote(work_dir)
        if remote:
            owner = remote.owner
            if options.verbose:
                print(f"Inferred owner '{owner}' from remote '{remote.url}'.")
    full_name = options.project if "/" in options.project or not owner else f"{owner}/{options.project}"
    clone_dir = work_dir / full_name.rsplit("/", 1)[-1]
    if clone_dir.exists():
        raise RuntimeError(f"Clone target already exists: '{clone_dir}'.")
    if shutil.which("gh") is None and not options.dry_run:
        raise RuntimeError("Cannot create repository because 'gh' is not installed.")

    command = ["gh", "repo", "create", full_name, options.visibility, "--clone"]
    for value, flag in (
        (options.description, "--description"), (options.homepage, "--homepage"),
        (options.gitignore, "--gitignore"), (options.license, "--license"),
        (options.remote, "--remote"), (options.team, "--team"), (options.template, "--template"),
    ):
        if value:
            command.extend([flag, value])
    for enabled, flag in (
        (options.add_readme, "--add-readme"),
        (options.disable_issues, "--disable-issues"),
        (options.disable_wiki, "--disable-wiki"),
    ):
        if enabled:
            command.append(flag)
    if options.dry_run and not owner and "/" not in options.project:
        print("No owner inferred; gh will use the authenticated account default.")
    if options.verbose and not options.dry_run:
        print_command(command, cwd=work_dir)
    run_command(command, cwd=work_dir, dry_run=options.dry_run)
    configure_upstream(clone_dir, options.remote or "origin", dry_run=options.dry_run)

@dataclass(frozen=True)
class SharedFile:
    """Path, basename and content of an existing file in the filesystem."""

    path: Path
    content: bytes
    digest: str

    @classmethod
    def load(cls, path):
        path = Path(path)
        content = path.read_bytes()
        return cls(path, content, hashlib.sha256(content).hexdigest())

    @property
    def basename(self):
        return self.path.name


@dataclass(frozen=True)
class FileVariant:
    """Different contents found for copies of one shared filename."""

    name: str
    files_by_content: dict
    newest: SharedFile
    others: list

    @property
    def files(self):
        return [file for files in self.files_by_content.values() for file in files]


def add_file(files, path, accepted_extensions, filename_filter):
    """Add a file when its extension and basename are accepted."""
    path = Path(path)
    ext = path.suffix.lstrip(".")
    if ext not in accepted_extensions:
        return
    if filename_filter and not re.fullmatch(filename_filter, path.name):
        return
    files.append(SharedFile.load(path))


def add_dir(files, path, accepted_extensions, filename_filter, excluded_names):
    """Add accepted files below a directory recursively."""
    for walk_path, walk_dirs, walk_files in os.walk(path):
        walk_dirs[:] = [d for d in walk_dirs if d not in excluded_names]
        for f in walk_files:
            if f in excluded_names:
                continue
            add_file(files, Path(walk_path) / f, accepted_extensions, filename_filter)


def collect_files(project_paths, accepted_extensions, filename_filter, excluded_names):
    """Collect accepted files from discovered project directories."""
    files = []
    for path in map(Path, project_paths):
        if not path.is_dir():
            raise RuntimeError(f"Discovered project path is not a directory: '{path}'")
        add_dir(files, path, accepted_extensions, filename_filter, excluded_names)
    return files


def print_diff(file_a, file_b):
    """Print diff.
    """
    a_text = file_a.content.decode("utf-8", errors="replace").splitlines(keepends=True)
    b_text = file_b.content.decode("utf-8", errors="replace").splitlines(keepends=True)
    diff = difflib.unified_diff(
        a_text,
        b_text,
        fromfile=str(file_a.path),
        tofile=str(file_b.path),
    )
    for line in diff:
        sys.stdout.write(line)


def copy_file(source_file, target_file):
    """Copy file.
    """
    print(f"Copying {source_file.path} -> {target_file.path}")
    shutil.copy2(source_file.path, target_file.path)


def get_newest_and_other(files):
    """Return the newest file and every other file."""
    newest = max(files, key=lambda file: file.path.stat().st_mtime)
    return newest, [file for file in files if file is not newest]


def get_unique_hash_prefixes(files, min_len=4):
    """Return a map of full hash to smallest unique prefix.
    """
    hashes = sorted(set(file.digest for file in files))
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


def find_variants(files):
    """Group shared files into synchronized names and differing variants."""
    files_by_name = {}
    for file in files:
        files_by_name.setdefault(file.basename, []).append(file)

    in_sync = []
    variants = []
    for name, matching_files in sorted(files_by_name.items()):
        if len(matching_files) < 2:
            continue
        files_by_content = {}
        for file in matching_files:
            files_by_content.setdefault(file.content, []).append(file)
        if len(files_by_content) == 1:
            in_sync.append((name, len(matching_files)))
            continue
        newest, others = get_newest_and_other(matching_files)
        variants.append(FileVariant(name, files_by_content, newest, others))
    return in_sync, variants


def report_variants(in_sync, variants):
    """Print synchronization status for analyzed shared files."""
    for name, count in in_sync:
        print(f"(File {name} is in sync across {count} files.)")
    for variant in variants:
        hash_prefixes = get_unique_hash_prefixes(variant.files)
        print(f"File {variant.name} exists in {len(variant.files_by_content)} variants:")
        for content, files in sorted(variant.files_by_content.items(), key=lambda item: len(item[0])):
            for file in files:
                date = datetime.datetime.fromtimestamp(
                    file.path.stat().st_mtime, tz=datetime.timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S")
                source = " (sync-source)" if file is variant.newest else ""
                print(
                    f"    hash={hash_prefixes[file.digest]} len={len(file.content):6d} "
                    f"date={date} {file.path}{source}"
                )


def print_variant_diffs(variants):
    """Print each distinct older content compared with its newest copy."""
    for variant in variants:
        files_by_content = {}
        for file in variant.others:
            files_by_content.setdefault(file.content, []).append(file)
        for files in files_by_content.values():
            for file in files:
                print(f"diff -u {file.path} {variant.newest.path}")
            print_diff(files[0], variant.newest)


def synchronize_variants(variants, check_git, verbose):
    """Validate targets and copy the newest content to every older copy."""
    if check_git:
        git_error = False
        for variant in variants:
            for file in variant.others:
                ok, message = check_git_clean_for_file(file)
                if verbose:
                    print(f"Git status for {file.path}: {'clean' if ok else 'modified'}")
                if not ok:
                    print(f"Error: {message}")
                    git_error = True
        if git_error:
            print("Error: Aborting sync due to git check failures.")
            return 1
    for variant in variants:
        for file in variant.others:
            copy_file(variant.newest, file)
    return 0

def get_repository_roots(project_paths):
    """Resolve repository roots once for the scanned project paths."""
    roots = set()
    for path in project_paths:
        result = run_command(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture=True,
            check=False,
        )
        if result.returncode == 0:
            roots.add(result.stdout.strip())
    return sorted(roots)

def repo_has_modifications(repo_root):
    """Return True if repo has tracked changes (staged or unstaged), ignoring untracked files."""
    status = run_command(
        ["git", "-C", repo_root, "status", "--porcelain", "--untracked-files=no"],
        capture=True,
        check=False,
    )
    if status.returncode != 0:
        return False
    return bool(status.stdout.strip())

def get_last_commit_message(repo_root):
    """Return the last commit message for the repo.
    """
    result = run_command(
        ["git", "-C", repo_root, "log", "-1", "--pretty=%B"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()

def check_git_clean_for_file(file):
    """Check whether the file itself is clean in its git repo.
    """
    repo_dir = file.path.parent
    result = run_command(
        ["git", "-C", repo_dir, "rev-parse", "--show-toplevel"],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        return (False, f"'{repo_dir}' is not in a git repo.")
    repo_root = result.stdout.strip()
    relpath = file.path.relative_to(repo_root)
    status = run_command(
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
        capture=True,
        check=False,
    )
    if status.returncode != 0:
        return (False, f"Failed to check git status in '{repo_root}'.")
    if status.stdout.strip():
        return (False, f"File '{file.path}' has local changes.")
    return (True, "")

def get_projects(workspace, accepted_extensions, excluded_names):
    """Return immediate child projects containing accepted source files."""
    projects = []
    workspace = Path(workspace)
    accepted_extensions = {extension.lstrip(".") for extension in accepted_extensions}
    ignored = {".git", ".svn", "__pycache__", "build", *excluded_names}
    try:
        children = sorted(workspace.iterdir(), key=lambda path: path.name)
    except OSError as e:
        raise RuntimeError(f"Cannot scan workspace '{workspace}': {e}")
    for child in children:
        if (
            not child.is_dir()
            or child.name.startswith(".")
            or child.name in ignored
        ):
            continue
        found = False
        for _, dirs, files in os.walk(child):
            dirs[:] = [name for name in dirs if name not in ignored and not name.startswith(".")]
            if any(
                name not in ignored
                and Path(name).suffix.lstrip(".") in accepted_extensions
                for name in files
            ):
                found = True
                break
        if found:
            projects.append(child.name)
    return projects


def run_sync_operation(options):
    """Compare or synchronize shared files in the scanned projects."""
    try:
        files = collect_files(
            options.project_paths, options.extensions, options.filter, options.exclude
        )
        in_sync, variants = find_variants(files)
        report_variants(in_sync, variants)
        if options.command == "status" and not variants:
            print("All shared files are in sync; sync_modules.py sync would copy nothing.")
        if options.command == "diff":
            print_variant_diffs(variants)
        if options.command == "sync":
            return synchronize_variants(
                variants, check_git=not options.no_git_check, verbose=options.verbose
            )
    except RuntimeError as e:
        print(f"Error: {e}")
        return 1
    return 0


def run_git_for_repositories(repo_roots, git_args):
    """Run the same Git command in each repository and aggregate failures."""
    return_code = 0
    for repo_root in repo_roots:
        print(f"Running git {' '.join(git_args)} in {repo_root}")
        result = run_command(["git", "-C", repo_root, *git_args], check=False)
        if result.returncode != 0:
            return_code = 1
    return return_code


GIT_COMMANDS = {
    "git-status": ["status", "--short", "--branch", "--untracked-files=no"],
    "git-diff": ["--no-pager", "diff"],
    "pull": ["pull", "--rebase"],
    "push": ["push"],
}


def run_git_operation(command, repo_roots):
    """Run one Git maintenance command on precomputed repositories."""
    if command in GIT_COMMANDS:
        return run_git_for_repositories(repo_roots, GIT_COMMANDS[command])
    if command != "commit":
        raise RuntimeError(f"Unsupported Git operation: {command}")

    commit_message_file = None
    try:
        for repo_root in repo_roots:
            if not repo_has_modifications(repo_root):
                print(f"Skipping commit in {repo_root} (no modifications)")
                continue
            print(f"Running git commit -a in {repo_root}")
            if commit_message_file is None:
                result = run_command(["git", "-C", repo_root, "commit", "-a"], check=False)
                if result.returncode != 0:
                    return 1
                commit_message = get_last_commit_message(repo_root)
                if not commit_message:
                    print("Warning: Empty commit message, skipping commits.")
                    return 1
                with tempfile.NamedTemporaryFile(mode="w+", delete=False) as tmp:
                    tmp.write(commit_message)
                    commit_message_file = tmp.name
            elif run_command(
                ["git", "-C", repo_root, "commit", "-a", "-F", commit_message_file],
                check=False,
            ).returncode != 0:
                return 1
    finally:
        if commit_message_file:
            os.unlink(commit_message_file)
    return 0


def load_config(path):
    """Load and validate persistent command defaults."""
    path = Path(path)
    if path == DEFAULT_CONFIG and not path.exists():
        try:
            with path.open("x", encoding="utf-8") as config_file:
                config_file.write(DEFAULT_CONFIG_CONTENT)
            print(f"Created default config '{path}'.", file=sys.stderr)
        except FileExistsError:
            # Another invocation created it after the existence check.
            pass
        except OSError as exc:
            raise RuntimeError(f"Cannot create default config '{path}': {exc}") from exc
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


def run_project_commands(command, projects, workspace, jobs):
    """Run a maintenance command for each selected project."""
    target = {"build": None, "unit-test": "unit_test", "clean": "clean"}[command]
    commands = []
    for project in projects:
        argv = ["make"]
        if command in ("unit-test", "clean"):
            argv += ["-j", str(jobs)]
        argv += ["-C", str(Path(workspace) / project)]
        if target:
            argv.append(target)
        commands.append((argv, workspace))

    def run(entry):
        argv, cwd = entry
        print("Running " + " ".join(argv), flush=True)
        return run_command(argv, cwd=cwd, check=False).returncode

    workers = jobs if command == "build" else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return 1 if any(pool.map(run, commands)) else 0


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Synchronize shared modules and maintain their projects.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="TOML config file (default: %(default)s).")
    parser.add_argument("--workspace", help="Override the workspace from the config file.")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    help_text = {
        "diff": "Show differences between synchronized module copies.",
        "sync": "Copy newest module versions to matching older copies.",
        "commit": "Commit changes in affected repositories.",
        "pull": "Pull affected repositories with rebase.",
        "push": "Push affected repositories.",
        "status": "Show synchronization status.",
        "git-status": "Show status of discovered Git repositories.",
        "git-diff": "Show diffs in affected repositories.",
        "build": "Build all relevant repositories.",
        "unit-test": "Build and run all relevant unit tests.",
        "clean": "Clean all relevant repositories.",
    }
    for name, description in help_text.items():
        command_parser = subparsers.add_parser(name, help=description, description=description)
        if name in ("diff", "sync", "status"):
            command_parser.add_argument("-f", "--filter", default="", help="Only process filenames matching this regex.")
        if name == "sync":
            command_parser.add_argument("--no-git-check", action="store_true", help="Allow overwriting locally modified files.")

    clone_parser = subparsers.add_parser("clone", help="Clone sibling GitHub repositories.")
    clone_parser.add_argument("project", nargs="?", help="Repository name to clone.")
    clone_mode = clone_parser.add_mutually_exclusive_group()
    clone_mode.add_argument("-l", "--list", action="store_true", help="List repositories for the inferred owner.")
    clone_mode.add_argument("-a", "--all", action="store_true", help="Clone all repositories not available locally.")

    new_parser = subparsers.add_parser("new", help="Create and clone a new GitHub repository.")
    new_parser.add_argument("project", help="Repository name, or OWNER/REPO.")
    new_parser.add_argument("-n", "--dry-run", action="store_true", help="Print commands without making changes.")
    new_parser.add_argument("-l", "--list-existing-projects", action="store_true", help="Search repositories instead of creating one.")
    new_parser.add_argument("-L", "--limit", type=int, default=30, help="Maximum search results (default: 30).")
    new_parser.add_argument("--owner", help="GitHub owner or organization.")
    visibility = new_parser.add_mutually_exclusive_group()
    visibility.add_argument("--private", action="store_const", const="--private", dest="visibility")
    visibility.add_argument("--public", action="store_const", const="--public", dest="visibility")
    visibility.add_argument("--internal", action="store_const", const="--internal", dest="visibility")
    new_parser.set_defaults(visibility="--public")
    new_parser.add_argument("-d", "--description")
    new_parser.add_argument("--homepage")
    new_parser.add_argument("-g", "--gitignore", help="GitHub gitignore template.")
    new_parser.add_argument("--license", help="GitHub license keyword.")
    new_parser.add_argument("--add-readme", action="store_true")
    new_parser.add_argument("--disable-issues", action="store_true")
    new_parser.add_argument("--disable-wiki", action="store_true")
    new_parser.add_argument("--remote", help="Remote name for the clone.")
    new_parser.add_argument("--team", help="Organization team to grant access.")
    new_parser.add_argument("-t", "--template", help="Template repository.")
    for command in subparsers._choices_actions:
        command_parser = subparsers.choices[command.dest]
        formatter = command_parser._get_formatter()
        arguments = [
            f"{formatter._format_action_invocation(action):<30} {action.help or ''}".rstrip()
            for action in command_parser._actions
            if not isinstance(action, argparse._HelpAction)
        ]
        if arguments:
            command.help += "\n" + "\n".join(arguments)
    return parser.parse_args()


def resolve_workspace(options, config):
    """Resolve the configured or command-line workspace path."""
    workspace = Path(options.workspace or config["workspace"])
    if not workspace.is_absolute():
        workspace = Path(options.config).resolve().parent / workspace
    return workspace.resolve()


def run_workspace_command(options, config, workspace):
    """Discover projects and dispatch one workspace command."""
    jobs = config["jobs"] or (os.cpu_count() or 1)
    if options.command == "clone":
        if not options.list and not options.all and not options.project:
            raise RuntimeError("Specify PROJECT, --list, or --all.")
        if options.project and (options.list or options.all):
            raise RuntimeError("PROJECT, --list, and --all are mutually exclusive.")
        run_clone_command(options, workspace)
        return 0
    if options.command == "new":
        run_new_command(options, workspace)
        return 0

    projects = get_projects(workspace, config["extensions"], config["exclude"])
    project_paths = [workspace / project for project in projects]
    if options.command in ("build", "unit-test", "clean"):
        build_projects = [
            project for project in projects
            if (workspace / project / "Makefile").is_file()
        ]
        return run_project_commands(options.command, build_projects, workspace, jobs)
    if options.command in ("commit", *GIT_COMMANDS):
        return run_git_operation(options.command, get_repository_roots(project_paths))

    operation = argparse.Namespace(
        project_paths=project_paths,
        command=options.command,
        extensions=config["extensions"],
        filter=options.filter,
        exclude=config["exclude"],
        no_git_check=getattr(options, "no_git_check", False),
        verbose=options.verbose,
    )
    return run_sync_operation(operation)


def main():
    options = parse_arguments()
    try:
        config = load_config(options.config)
        workspace = resolve_workspace(options, config)
        return run_workspace_command(options, config, workspace)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1



# Call main().
if __name__ == "__main__":
    sys.exit(main())
