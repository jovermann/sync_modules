#!/usr/bin/python3
#
# diff_src.py - diff sources
#
# Copyright (C) 2024-2025 by Johannes Overmann <Johannes.Overmann@joov.de>

import argparse
import os
import re
import datetime
import hashlib
import difflib
import shutil
import subprocess
import sys
import tempfile

extensions = ""
filter = ""
exclude = []

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


def main():
    """Main function of this module.
    """
    global extensions, filter, exclude
    usage = """Usage: %(prog)s [OPTIONS] DIRS... FILES...
    """
    version = "0.0.1"
    parser = argparse.ArgumentParser(usage = usage + "\n(Version " + version + ")\n")
    parser.add_argument("args", nargs="*", help="Dirs and files to process.")
    parser.add_argument("-x", "--extensions", help="Specify valid source file extensions.", type=str, default="c,h,cpp,hpp,cxx,hxx")
    parser.add_argument("-f", "--filter", help="Filter all filenames using this regex.", type=str, default="")
    parser.add_argument("-e", "--exclude", help="Ignore dirs and/or files. May be specified multiple times.", default=[], action="append")
    parser.add_argument("-d", "--diff", help="Print diff.", action="store_true")
    parser.add_argument("-s", "--sync", help="Synchronize files automatically.", action="store_true")
    parser.add_argument("--pull", help="Run git pull --rebase on involved repos.", action="store_true")
    parser.add_argument("--push", help="Run git push on involved repos.", action="store_true")
    parser.add_argument("--commit", help="Run git commit -a using the git editor message.", action="store_true")
    parser.add_argument("--no-git-check", help="Disable git repo cleanliness check.", action="store_true")
    parser.add_argument("-V", "--verbose", help="Be more verbose. May be specified multiple times.", action="count", default=0) # -v is taken by --version, argh!
    options = parser.parse_args()

    extensions = options.extensions.split(',')
    filter = options.filter
    exclude = options.exclude

    try:
        # Read all files and dirs.
        fileListAll = []
        for path in options.args:
            if not os.path.exists(path):
                print("Error: Path '{}' does not exist.\n".format(path))
            elif os.path.isfile(path):
                addFile(fileListAll, path)
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
                    print(f"    hash={hash_prefixes[file.hash]} len={len(file.content):6d} date={date} {file.path}")

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

        if options.pull or options.push or options.commit:
            repo_roots = getRepoRootsFromFiles(involved_files)
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
        return



# Call main().
if __name__ == "__main__":
    main()
