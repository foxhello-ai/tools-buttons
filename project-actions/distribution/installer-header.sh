#!/bin/bash
set -euo pipefail
if [ "${1:-}" = --help ]; then
  cat <<'HELP'
Project Actions — 一个文件，接入当前项目（macOS 13+，Intel / Apple Silicon）
  bash project-actions-macos.run [项目路径] [--no-launch]
  bash project-actions-macos.run --project /path/to/project
高级选项：--project-id ID --install-dir DIR --bin-dir DIR
HELP
  exit 0
fi
if [ "$(uname -s)" != Darwin ]; then
  printf '%s\n' 'This installer requires macOS 13 or newer.' >&2
  exit 1
fi
mac_version="$(sw_vers -productVersion)"
if [ "${mac_version%%.*}" -lt 13 ]; then
  printf '%s\n' 'This installer requires macOS 13 or newer.' >&2
  exit 1
fi
case "$(uname -m)" in
  arm64) runtime_arch=arm64 ;;
  x86_64) runtime_arch=x86_64 ;;
  *) printf '%s\n' 'Unsupported CPU architecture' >&2; exit 1 ;;
esac
script_source="${BASH_SOURCE[0]:-}"
install_payload() {
  installer_tmp="$(mktemp -d "${TMPDIR:-/tmp}/project-actions.XXXXXX")"
  trap 'rm -rf "$installer_tmp"' EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  printf '%s\n' '正在解包 Project Actions…'
  # Let base64 consume the stream directly; macOS Bash is slow parsing a huge heredoc.
  if [ -n "$script_source" ] && [ -f "$script_source" ]; then
    /usr/bin/tail -n +@@PAYLOAD_LINE@@ "$script_source" | base64 -D > "$installer_tmp/payload.tar.gz"
  else
    base64 -D > "$installer_tmp/payload.tar.gz"
  fi
  payload_digest="$(/usr/bin/shasum -a 256 "$installer_tmp/payload.tar.gz")"
  payload_digest="${payload_digest%% *}"
  if [ "$payload_digest" != "@@CHECKSUM@@" ]; then
    printf '%s\n' '安装包不完整或校验失败，未执行安装。' >&2
    exit 1
  fi
  /usr/bin/tar -xzf "$installer_tmp/payload.tar.gz" -C "$installer_tmp"
  export PYTHONDONTWRITEBYTECODE=1
  "$installer_tmp/payload/ProjectActions.app/Contents/Resources/Python-$runtime_arch/bin/python3" \
    -I -B "$installer_tmp/payload/install.py" "$@" </dev/null
}
install_payload "$@"; exit $?
