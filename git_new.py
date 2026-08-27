#!/usr/bin/python3
#
# git_new.py - create and clone a new GitHub repository
#
# Copyright (C) 2026 by Johannes Overmann <Johannes.Overmann@joov.de>
#
# Distributed under the Boost Software License, Version 1.0.
# (See accompanying file LICENSE or copy at https://www.boost.org/LICENSE_1_0.txt)

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys


class Remote:
    """Parsed GitHub remote URL."""

    def __init__(self, url, host, owner, repo):
        self.url = url
        self.host = host
        self.owner = owner
        self.repo = repo


def run(args, cwd=None, capture=False, check=True, dry_run=False):
    """Run a command, or print it for dry-run mode."""
    if dry_run:
        printCommand(args, cwd=cwd)
        return None

    kwargs = {}
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = subprocess.run(args, cwd=cwd, **kwargs)
    if check and result.returncode != 0:
        command = " ".join(args)
        if capture and result.stderr:
            raise RuntimeError(f"Command failed: {command}\n{result.stderr.strip()}")
        raise RuntimeError(f"Command failed: {command}")
    return result


def printCommand(args, cwd=None):
    """Print a shell-style representation of a command."""
    prefix = f"(cd {shlex.quote(cwd)} && " if cwd else "("
    print(prefix + " ".join(shlex.quote(arg) for arg in args) + ")")


def parseGithubRemote(url):
    """Parse the common GitHub remote URL forms."""
    patterns = [
        r"^https://([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@([^:]+):([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@([^/]+)/([^/]+)/([^/]+?)(?:\.git)?/?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return Remote(url, match.group(1), match.group(2), match.group(3))
    return None


def getRemoteUrl(repo_dir):
    """Return a likely fetch remote URL from repo_dir, or None."""
    remote_names = ["origin"]
    remotes = run(
        ["git", "-C", repo_dir, "remote"],
        capture=True,
        check=False,
    )
    if remotes.returncode == 0:
        remote_names.extend(
            name for name in remotes.stdout.splitlines() if name and name != "origin"
        )

    for remote_name in remote_names:
        result = run(
            ["git", "-C", repo_dir, "remote", "get-url", remote_name],
            capture=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def findRemote(start_dir):
    """Find and parse a GitHub remote from start_dir or one of its subdirs."""
    candidates = []
    if os.path.isdir(os.path.join(start_dir, ".git")):
        candidates.append(start_dir)

    try:
        entries = sorted(os.listdir(start_dir))
    except OSError as e:
        raise RuntimeError(f"Cannot inspect '{start_dir}': {e}") from e

    for entry in entries:
        path = os.path.join(start_dir, entry)
        if os.path.isdir(os.path.join(path, ".git")):
            candidates.append(path)

    for repo_dir in candidates:
        url = getRemoteUrl(repo_dir)
        if not url:
            continue
        remote = parseGithubRemote(url)
        if remote:
            return remote

    return None


def validateProjectName(project):
    """Reject values which look like paths rather than GitHub repository names."""
    if project in ("", ".", ".."):
        raise RuntimeError("Repository name must not be empty, '.', or '..'.")
    if project.startswith("/") or project.endswith("/"):
        raise RuntimeError("Repository name must not start or end with '/'.")
    if "\\" in project:
        raise RuntimeError("Repository name must not contain backslashes.")
    if project.count("/") > 1:
        raise RuntimeError("Use REPO or OWNER/REPO, not a path.")


def targetDir(work_dir, project):
    """Return the local clone target directory."""
    return os.path.join(work_dir, project.rsplit("/", 1)[-1])


def makeFullName(project, owner):
    """Return OWNER/REPO when owner inference or override is available."""
    if "/" in project:
        return project
    if owner:
        return f"{owner}/{project}"
    return project


def configureUpstream(clone_dir, remote_name, dry_run=False):
    """Configure the initial branch to track its same-named remote branch.

    A newly cloned empty repository has no remote branch yet, so
    ``git branch --set-upstream-to`` cannot be used.  Setting the underlying
    branch configuration works before the first commit and makes a plain
    ``git push`` behave like the initial ``git push --set-upstream``.
    """
    branch = "main"
    if not dry_run:
        result = run(
            ["git", "-C", clone_dir, "symbolic-ref", "--short", "HEAD"],
            capture=True,
        )
        branch = result.stdout.strip()
        if not branch:
            raise RuntimeError(f"Cannot determine the initial branch in '{clone_dir}'.")

    run(
        ["git", "config", f"branch.{branch}.remote", remote_name],
        cwd=clone_dir,
        dry_run=dry_run,
    )
    run(
        ["git", "config", f"branch.{branch}.merge", f"refs/heads/{branch}"],
        cwd=clone_dir,
        dry_run=dry_run,
    )


def createRepository(options):
    """Create the remote repository and clone it locally."""
    work_dir = os.path.abspath(options.directory)
    validateProjectName(options.project)

    if not os.path.isdir(work_dir):
        raise RuntimeError(f"Workspace directory does not exist: '{work_dir}'.")

    owner = options.owner
    if not owner and "/" not in options.project:
        remote = findRemote(work_dir)
        if remote:
            owner = remote.owner
            if options.verbose:
                print(f"Inferred owner '{owner}' from remote '{remote.url}'.")

    full_name = makeFullName(options.project, owner)
    clone_dir = targetDir(work_dir, full_name)
    if options.verbose:
        print(f"Repository: {full_name}")
        print(f"Clone target: {clone_dir}")
    if os.path.exists(clone_dir):
        raise RuntimeError(f"Clone target already exists: '{clone_dir}'.")

    if shutil.which("gh") is None and not options.dry_run:
        raise RuntimeError("Cannot create repository because 'gh' is not installed.")

    command = ["gh", "repo", "create", full_name, options.visibility, "--clone"]

    if options.description:
        command.extend(["--description", options.description])
    if options.homepage:
        command.extend(["--homepage", options.homepage])
    if options.gitignore:
        command.extend(["--gitignore", options.gitignore])
    if options.license:
        command.extend(["--license", options.license])
    if options.add_readme:
        command.append("--add-readme")
    if options.disable_issues:
        command.append("--disable-issues")
    if options.disable_wiki:
        command.append("--disable-wiki")
    if options.remote:
        command.extend(["--remote", options.remote])
    if options.team:
        command.extend(["--team", options.team])
    if options.template:
        command.extend(["--template", options.template])

    if options.dry_run and not owner and "/" not in options.project:
        print("No owner inferred; gh will use the authenticated account default.")

    if options.verbose and not options.dry_run:
        printCommand(command, cwd=work_dir)

    run(command, cwd=work_dir, dry_run=options.dry_run)
    configureUpstream(
        clone_dir,
        options.remote if options.remote else "origin",
        dry_run=options.dry_run,
    )


def listExistingProjects(options):
    """Search existing GitHub repositories without creating anything."""
    if options.limit <= 0:
        raise RuntimeError("--limit must be greater than zero.")

    if shutil.which("gh") is None and not options.dry_run:
        raise RuntimeError("Cannot search repositories because 'gh' is not installed.")

    command = [
        "gh",
        "search",
        "repos",
        "--limit",
        str(options.limit),
        "--",
        options.project,
    ]

    if options.verbose and not options.dry_run:
        printCommand(command)

    run(command, dry_run=options.dry_run)


def main():
    usage = """Usage: %(prog)s [OPTIONS] PROJ
    """
    version = "0.0.1"
    parser = argparse.ArgumentParser(usage=usage + "\n(Version " + version + ")\n")
    parser.add_argument("project", help="Repository name, or OWNER/REPO.")
    parser.add_argument(
        "-C",
        "--directory",
        default=".",
        help="Workspace directory to inspect and clone into. Defaults to current dir.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print the gh command without creating or cloning the repository.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print owner inference, target path, and command details.",
    )
    parser.add_argument(
        "-l",
        "--list-existing-projects",
        action="store_true",
        help="Search existing GitHub repositories matching PROJ instead of creating one.",
    )
    parser.add_argument(
        "-L",
        "--limit",
        type=int,
        default=30,
        help="Maximum number of repositories to list. Defaults to 30.",
    )
    parser.add_argument(
        "--owner",
        help="GitHub owner or organization. Defaults to owner inferred from nearby clones.",
    )
    visibility = parser.add_mutually_exclusive_group()
    visibility.add_argument(
        "--private",
        action="store_const",
        const="--private",
        dest="visibility",
        help="Create a private repository.",
    )
    visibility.add_argument(
        "--public",
        action="store_const",
        const="--public",
        dest="visibility",
        help="Create a public repository. This is the default.",
    )
    visibility.add_argument(
        "--internal",
        action="store_const",
        const="--internal",
        dest="visibility",
        help="Create an internal organization repository.",
    )
    parser.set_defaults(visibility="--public")
    parser.add_argument("-d", "--description", help="Repository description.")
    parser.add_argument("--homepage", help="Repository home page URL.")
    parser.add_argument("-g", "--gitignore", help="GitHub gitignore template.")
    parser.add_argument("--license", help="GitHub license keyword.")
    parser.add_argument(
        "--add-readme",
        action="store_true",
        help="Add a README file to the new repository.",
    )
    parser.add_argument(
        "--disable-issues",
        action="store_true",
        help="Disable issues in the new repository.",
    )
    parser.add_argument(
        "--disable-wiki",
        action="store_true",
        help="Disable wiki in the new repository.",
    )
    parser.add_argument("--remote", help="Remote name for the cloned repository.")
    parser.add_argument("--team", help="Organization team to grant access.")
    parser.add_argument(
        "-t",
        "--template",
        help="Create the repository from a template repository.",
    )

    options = parser.parse_args()

    try:
        if options.list_existing_projects:
            listExistingProjects(options)
        else:
            createRepository(options)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
