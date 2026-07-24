#!/usr/bin/python3
#
# git_clone.py - clone sibling GitHub repositories by short project name
#
# Copyright (C) 2026 by Johannes Overmann <Johannes.Overmann@joov.de>
#
# Distributed under the Boost Software License, Version 1.0.
# (See accompanying file LICENSE or copy at https://www.boost.org/LICENSE_1_0.txt)

import argparse
import os
import re
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

    def urlForProject(self, project):
        """Return a remote URL like this one, but with a different repo name."""
        if self.url.endswith("/" + self.repo):
            return self.url[: -len(self.repo)] + project
        if self.url.endswith("/" + self.repo + ".git"):
            return self.url[: -len(self.repo + ".git")] + project + ".git"
        if self.url.endswith(":" + self.owner + "/" + self.repo):
            return self.url[: -len(self.repo)] + project
        if self.url.endswith(":" + self.owner + "/" + self.repo + ".git"):
            return self.url[: -len(self.repo + ".git")] + project + ".git"
        raise RuntimeError(f"Cannot rewrite remote URL '{self.url}'.")


def run(args, cwd=None, capture=False, check=True):
    """Run a command."""
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

    raise RuntimeError(
        f"Found no GitHub remote in '{start_dir}' or its direct subdirectories."
    )


def listProjects(remote):
    """List GitHub projects owned by the inferred owner using gh."""
    if shutil.which("gh") is None:
        raise RuntimeError("Cannot list projects because 'gh' is not installed.")

    result = run(
        [
            "gh",
            "repo",
            "list",
            remote.owner,
            "--limit",
            "1000",
            "--json",
            "name",
            "--jq",
            ".[].name",
        ],
        capture=True,
    )
    return sorted(name for name in result.stdout.splitlines() if name)


def cloneProject(remote, project, work_dir):
    """Clone project into work_dir if it is not already available."""
    target = os.path.join(work_dir, project)
    if os.path.exists(target):
        print(f"Skipping {project} (already exists)")
        return
    url = remote.urlForProject(project)
    print(f"Cloning {url}")
    run(["git", "clone", url, project], cwd=work_dir)


def main():
    usage = """Usage: %(prog)s [OPTIONS] [PROJ]
    """
    version = "0.0.1"
    parser = argparse.ArgumentParser(usage=usage + "\n(Version " + version + ")\n")
    parser.add_argument("project", nargs="?", help="Project/repository name to clone.")
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List all GitHub projects for the inferred owner using gh.",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Clone all projects which are not yet locally available.",
    )
    parser.add_argument(
        "-C",
        "--directory",
        default=".",
        help="Workspace directory to inspect and clone into. Defaults to current dir.",
    )
    options = parser.parse_args()

    try:
        work_dir = os.path.abspath(options.directory)
        remote = findRemote(work_dir)

        if options.list:
            for project in listProjects(remote):
                print(project)

        if options.all:
            for project in listProjects(remote):
                cloneProject(remote, project, work_dir)

        if options.project:
            cloneProject(remote, options.project, work_dir)

        if not options.list and not options.all and not options.project:
            parser.error("Specify PROJ, --list, or --all.")

    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
