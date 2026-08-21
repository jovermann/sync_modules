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

## git_new.py

Create a new GitHub project and clone it into the current workspace:

```sh
sync_modules/git_new.py my_new_tool
```

Before creating a repository, use `-l` / `--list-existing-projects`
to search for existing GitHub projects with a candidate name:

```sh
sync_modules/git_new.py -l my_new_tool
sync_modules/git_new.py -l "my new tool in:name"
sync_modules/git_new.py -l -L 10 "mytool in:name stars:>5"
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
sync_modules/git_new.py --dry-run my_new_tool
```
