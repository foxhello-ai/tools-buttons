#!/bin/bash
set -euo pipefail
demo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_dir="${1:-$PWD}"
if [ "$#" -gt 0 ]; then shift; fi
project_dir="$(cd -- "$project_dir" && pwd)"
if [ ! -f "$project_dir/.project-actions/actions.json" ]; then
  printf '%s\n' "Missing $project_dir/.project-actions/actions.json; initialize the project first." >&2
  exit 1
fi
open -n "$demo_dir/DirectoryButton.app" --args --directory "$project_dir" "$@"
