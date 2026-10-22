#!/bin/bash
set -euo pipefail
demo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_dir="${PROJECT_ACTIONS_APP_OUTPUT:-$demo_dir/DirectoryButton.app}"
target_arch="${PROJECT_ACTIONS_ARCH:-$(uname -m)}"
mkdir -p "$app_dir/Contents/MacOS" "$app_dir/Contents/Resources"
swift_sources=("$demo_dir/ProjectVisibility.swift" "$demo_dir/ButtonPlacement.swift" "$demo_dir/ActionService.swift" "$demo_dir/TaskWindow.swift" "$demo_dir/DirectoryButton.swift")
if [ "$target_arch" = universal ]; then
  build_tmp="$(mktemp -d -t project-actions-native)"
  trap 'rm -rf "$build_tmp"' EXIT
  for arch in x86_64 arm64; do
    xcrun swiftc "${swift_sources[@]}" -target "$arch-apple-macos13.0" -framework AppKit -framework WebKit -o "$build_tmp/$arch"
  done
  xcrun lipo -create "$build_tmp/x86_64" "$build_tmp/arm64" -output "$app_dir/Contents/MacOS/DirectoryButton"
else
  xcrun swiftc "${swift_sources[@]}" -target "$target_arch-apple-macos13.0" -framework AppKit -framework WebKit -o "$app_dir/Contents/MacOS/DirectoryButton"
fi
python3 - "$app_dir" "$demo_dir" <<'PY'
import plistlib
import json
import os
import sys
import shutil
from pathlib import Path
app_dir, demo_dir = map(Path, sys.argv[1:])
project_directory = str(demo_dir.parents[1] if demo_dir.parent.name == 'deliverables' else demo_dir.parent)
project_id = None
runtime = demo_dir.parent / 'project-actions'
bundled = app_dir / 'Contents/Resources/ProjectActions'
bundled.mkdir(parents=True, exist_ok=True)
for name in ('project_actions.py', 'action_inputs.py', 'schema.json', 'AI_GUIDE.md', 'PROTOCOL.md', 'PARAMETERS_AND_RESUME.md'):
    shutil.copy2(runtime / name, bundled / name)
for name in ('web', 'shell', 'templates', 'examples'):
    shutil.copytree(runtime / name, bundled / name, dirs_exist_ok=True)
state_path = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / '.codex-global-state.json'
try:
    state = json.loads(state_path.read_text())
    assignment = state.get('thread-project-assignments', {}).get(os.environ.get('CODEX_THREAD_ID'), {})
    candidate = assignment.get('projectId') if assignment.get('projectKind') == 'local' else None
    roots = state.get('local-projects', {}).get(candidate, {}).get('rootPaths', [])
    if any(Path(root).resolve() == Path(project_directory).resolve() for root in roots):
        project_id = candidate
except (OSError, ValueError, TypeError):
    pass
with (app_dir / 'Contents' / 'Info.plist').open('wb') as file:
    plistlib.dump({
        'CFBundleExecutable': 'DirectoryButton',
        'CFBundleIdentifier': 'local.demo.directory-button',
        'CFBundleName': '目录按钮',
        'CFBundleVersion': '4',
        'CFBundlePackageType': 'APPL',
        'LSUIElement': True,
        'LSMinimumSystemVersion': '13.0',
        'NSHighResolutionCapable': True,
        'ProjectDirectory': project_directory,
        'PythonExecutable': sys.executable,
        **({'ProjectID': project_id} if project_id else {}),
    }, file)
PY
codesign --force --sign - "$app_dir"
printf '%s\n' "$app_dir"
