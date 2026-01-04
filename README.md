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


