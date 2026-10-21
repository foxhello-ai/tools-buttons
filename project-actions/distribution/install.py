#!/usr/bin/env python3
"""Install one immutable release for the current user, then attach a project."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

PAYLOAD = Path(__file__).resolve().parent
MARKER = '# Managed by Project Actions'


def main():
    parser = argparse.ArgumentParser(description='安装 Project Actions 并接入当前项目')
    parser.add_argument('directory', nargs='?')
    parser.add_argument('--project')
    parser.add_argument('--no-launch', action='store_true')
    parser.add_argument('--project-id')
    parser.add_argument('--install-dir', type=Path, default=Path.home() / 'Library/Application Support/ProjectActions')
    parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin')
    arguments = parser.parse_args()
    if arguments.directory and arguments.project:
        parser.error('项目路径只需提供一次')
    project = Path(arguments.project or arguments.directory or os.getcwd()).expanduser().resolve()
    if not project.is_dir():
        parser.error('项目目录不存在：%s' % project)
    install_root = arguments.install_dir.expanduser().resolve()
    bin_dir = arguments.bin_dir.expanduser().resolve()
    command_file = bin_dir / 'project-actions'
    try:
        if command_file.is_symlink() or (command_file.exists() and
                (not command_file.is_file() or MARKER not in command_file.read_text(encoding='utf-8'))):
            raise RuntimeError('已有同名命令，未覆盖：%s；可用 --bin-dir 指定其他位置' % command_file)
        manifest = json.loads((PAYLOAD / 'manifest.json').read_text())
        release_name = manifest['version'] + '-' + manifest['buildId']
        releases = install_root / 'releases'
        releases.mkdir(parents=True, exist_ok=True)
        installed = releases / release_name
        if not installed.exists():
            stage = Path(tempfile.mkdtemp(prefix='.install-', dir=str(releases)))
            try:
                shutil.copytree(PAYLOAD, stage / release_name, symlinks=True)
                try:
                    os.rename(str(stage / release_name), str(installed))
                except OSError:
                    if not installed.is_dir():
                        raise
            finally:
                shutil.rmtree(stage)
        temporary_link = install_root / ('.current-' + uuid.uuid4().hex)
        try:
            temporary_link.symlink_to(Path('releases') / release_name, target_is_directory=True)
            os.replace(str(temporary_link), str(install_root / 'current'))
        finally:
            temporary_link.unlink(missing_ok=True)
        bin_dir.mkdir(parents=True, exist_ok=True)
        temporary_command = bin_dir / ('.project-actions-' + uuid.uuid4().hex)
        wrapper = '#!/bin/bash\n' + MARKER + '\nexec ' + shlex.quote(str(install_root / 'current/project-actions')) + ' "$@"\n'
        try:
            temporary_command.write_text(wrapper, encoding='utf-8')
            temporary_command.chmod(0o755)
            os.replace(str(temporary_command), str(command_file))
        finally:
            temporary_command.unlink(missing_ok=True)
        command = [str(installed / 'project-actions'), 'add', str(project)]
        if arguments.no_launch:
            command.append('--no-launch')
        if arguments.project_id:
            command += ['--project-id', arguments.project_id]
        subprocess.run(command, check=True)
        print('安装完成。其他项目只需执行：%s add .' % shlex.quote(str(command_file)))
        return 0
    except (OSError, ValueError, RuntimeError, UnicodeError, subprocess.CalledProcessError) as error:
        print('安装未完成：%s' % error, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
