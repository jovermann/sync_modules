#!/bin/sh

set -eu

usage() {
    echo "Usage: $0 [--force]" >&2
}

force=0
case "${1:-}" in
    "")
        ;;
    --force)
        force=1
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage
        exit 2
        ;;
esac

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_name=$(basename -- "$script_dir")
parent_dir=$(dirname -- "$script_dir")
link_path=$parent_dir/Makefile
link_target=$repo_name/Makefile

if [ -e "$link_path" ] && [ ! -L "$link_path" ] && [ "$force" -ne 1 ]; then
    echo "Refusing to replace existing regular file: $link_path" >&2
    echo "Run with --force to replace it." >&2
    exit 1
fi

ln -sfn -- "$link_target" "$link_path"
echo "$link_path -> $link_target"
