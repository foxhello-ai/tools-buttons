#!/bin/bash
set -euo pipefail
demo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
verify_dir="$(mktemp -d -t directory-button-verify)"
trap 'rm -rf "$verify_dir"' EXIT
"$demo_dir/DirectoryButton.app/Contents/MacOS/DirectoryButton" \
  --verify-drag \
  --preferences-suite "local.demo.directory-button.verify-drag.$RANDOM.$RANDOM"
"$demo_dir/DirectoryButton.app/Contents/MacOS/DirectoryButton" \
  --verify-lifecycle \
  --state-file "$verify_dir/state.json" \
  --preferences-suite "local.demo.directory-button.verify.$RANDOM.$RANDOM"
"$demo_dir/DirectoryButton.app/Contents/MacOS/DirectoryButton" \
  --verify-hover --snapshot "$verify_dir/hover.png"
test -s "$verify_dir/hover.png"
python3 "$demo_dir/verify-service-integration.py"
codesign --verify --strict "$demo_dir/DirectoryButton.app"
