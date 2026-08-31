# sync_modules.py

Compare and synchronize source files across source directories.

The main purpose of this tool is to synchronize changes to a set of
files shared between different git repositories.
The obvious solutions would be to deduplicate the shared files 
and move them into a library shared by all projects. This has the
rather big disadvantage that this does not scale well. Projects
will pull in functionality that they do not need. And projects
will have one or more external dependencies even for basic
functionality like string helpers or command line parsing.

Instead all these shared files are duplicated across all 
repositories and they are kept in sync by this scripts.

The main advantage is that each project using one or more
of the shared files does not have any external dependencies
and is self-contained.

This tool was developed with the help of codex.

## Commands

Run commands from any directory. The workspace is read from the default
configuration file:

```sh
sync_modules.py status
sync_modules.py git-status
sync_modules.py diff
sync_modules.py sync
sync_modules.py commit
sync_modules.py pull
sync_modules.py push
sync_modules.py git-diff
sync_modules.py clone PROJECT
sync_modules.py clone --list
sync_modules.py clone --all
sync_modules.py new PROJECT
sync_modules.py build
sync_modules.py unit-test
sync_modules.py clean
```

Local maintenance commands operate on projects discovered from the configured
workspace. `clone` and `new` accept remote repository names. Use `--workspace`
to temporarily override the workspace.

Persistent settings are in `~/.sync_modules.toml`. It defines the workspace,
parallel job count, source extensions, and exclusions used by every command.
Projects containing a configured source extension are discovered automatically.
A `jobs` value of `0` uses the available CPU count. Use `--config` to select a
different configuration file.

## Creating repositories

Create a new GitHub project and clone it into the current workspace:

```sh
sync_modules.py new my_new_tool
```

The new repository's initial branch is configured to track the matching branch
on `origin`, including when the remote repository is still empty. After the
first commit, a plain `git push` creates and updates the remote branch; no
initial `--set-upstream` option is needed.

Before creating a repository, use `-l` / `--list-existing-projects`
to search for existing GitHub projects with a candidate name:

```sh
sync_modules.py new -l my_new_tool
sync_modules.py new -l "my new tool in:name"
sync_modules.py new -l -L 10 "mytool in:name stars:>5"
```

This is useful for trying out projected names and choosing one that is
unused or less used. The pattern is passed to `gh search repos`, so it
uses GitHub repository search syntax rather than shell globs or regular
expressions. Useful qualifiers include:

```text
in:name
in:name,description
user:USERNAME
org:ORGNAME
language:LANGUAGE
stars:>10
topic:TOPIC
archived:false
```

Use `--dry-run` to see the `gh` command without creating anything:

```sh
sync_modules.py new --dry-run my_new_tool
```

## Cloning repositories

Clone a sibling repository using the GitHub owner and URL style inferred from
an existing workspace clone:

```sh
sync_modules.py clone project_name
```

Use `clone --list` to list repositories for the inferred owner and
`clone --all` to clone every repository that is not already present locally.
