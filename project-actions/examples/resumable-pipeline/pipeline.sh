#!/usr/bin/env bash
set -Eeuo pipefail
# options/preview are read-only and have no event token. run reports real events.
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PROJECT_ACTIONS_PYTHON:-python3}" "$script_dir/pipeline.py" "${1:?options / preview / run}"
