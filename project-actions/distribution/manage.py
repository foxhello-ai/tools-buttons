#!/usr/bin/env python3
"""Installed project-actions command; all paths are relative to this release."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

RELEASE = Path(__file__).resolve().parent
APP = RELEASE / 'ProjectActions.app'
RESOURCES = APP / 'Contents/Resources'
TOOL = RESOURCES / 'ProjectActions'
sys.path.insert(0, str(TOOL))
import project_actions as actions

AGENT_START = '<!-- project-actions:start -->'
AGENT_END = '<!-- project-actions:end -->'
AGENT_ENTRY = '''<!-- project-actions:start -->
## 项目操作按钮

新增或修改项目操作按钮时，先阅读 `.project-actions/AI_GUIDE.md`。
按钮定义在 `.project-actions/actions.json`，业务脚本在 `.project-actions/scripts/`。
沿用日志、节点、进度和产物上报接口，保留已有按钮与项目标识。
<!-- project-actions:end -->
'''


def attach_project(project):
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise actions.Problem('项目目录不存在：%s' % project)
    config = project / actions.CONFIG_PATH
    if not config.exists():
        actions.initialize(project)
    # Validate existing configuration before adding documentation; never replace it.
    actions.load_config(project)
    destination = config.parent
    for name in ('AI_GUIDE.md', 'PROTOCOL.md', 'schema.json'):
        if not (destination / name).exists():
            shutil.copy2(TOOL / name, destination / name)
    if not (destination / 'AGENTS.md').exists():
        shutil.copy2(TOOL / 'templates/AGENTS.md.template', destination / 'AGENTS.md')
    ignore = destination / '.gitignore'
    existing = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    missing = [line for line in ('runs/', '.runtime*') if line not in existing.splitlines()]
    if missing:
        with ignore.open('a', encoding='utf-8') as stream:
            stream.write(('\n' if existing and not existing.endswith('\n') else '') + '\n'.join(missing) + '\n')
    root_entry = project / 'AGENTS.md'
    original = root_entry.read_text(encoding='utf-8') if root_entry.exists() else ''
    # Respect existing root guidance; the marked entry is appended at most once.
    if AGENT_START not in original:
        with root_entry.open('a', encoding='utf-8') as stream:
            stream.write(('\n\n' if original else '') + AGENT_ENTRY)
    return project


def codex_project_id(project):
    state_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    try:
        state = json.loads((state_home / '.codex-global-state.json').read_text(encoding='utf-8'))
        assignment = state.get('thread-project-assignments', {}).get(os.environ.get('CODEX_THREAD_ID'), {})
        selected = state.get('selected-project', {})
        candidates = []
        if assignment.get('projectKind') == 'local':
            candidates.append(assignment.get('projectId'))
        if selected.get('type') == 'local':
            candidates.append(selected.get('projectId'))
        for candidate in candidates:
            roots = state.get('local-projects', {}).get(candidate, {}).get('rootPaths', [])
            if any(Path(root).resolve() == project for root in roots):
                return candidate
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def desktop_is_running(project):
    try:
        state = json.loads((project / '.project-actions/.runtime-desktop.json').read_text(encoding='utf-8'))
        pid = state['pid']
        if type(pid) is not int or pid <= 0 or Path(state['directory']).resolve() != project:
            return False
        executable = Path(state['executable'])
        # Check the PID's executable as well, so a stale file cannot match an unrelated process.
        command = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'comm='],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        return (command.returncode == 0 and command.stdout.strip() == str(executable)
                and executable.name == 'DirectoryButton'
                and executable.parent.parent.parent.name == 'ProjectActions.app')
    except (OSError, ValueError, KeyError, TypeError):
        return False


def open_desktop(project, project_id=None):
    if desktop_is_running(project):
        print('桌面按钮已在运行；显隐可通过菜单栏控制。')
        return
    command = ['/usr/bin/open', '-n', str(APP), '--args', '--directory', str(project),
               '--diagnostics-file', str(project / '.project-actions/.runtime-desktop.json')]
    project_id = project_id or codex_project_id(project)
    if project_id:
        command += ['--project-id', project_id]
    subprocess.run(command, check=True)
    print('桌面按钮已启动，默认随 Codex 中对应项目显示／隐藏。')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    known = {'add', 'open', 'validate', 'web', 'version'}
    if not argv:
        argv = ['add', '.']
    elif not argv[0].startswith('-') and argv[0] not in known:
        argv.insert(0, 'add')
    parser = argparse.ArgumentParser(description='Project Actions：一条命令接入项目')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('add', 'open', 'validate', 'web'):
        command = commands.add_parser(name)
        command.add_argument('project', nargs='?', default='.')
        if name in {'add', 'open'}:
            command.add_argument('--project-id')
        if name == 'add':
            command.add_argument('--no-launch', action='store_true')
    commands.add_parser('version')
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == 'version':
            manifest = json.loads((RELEASE / 'manifest.json').read_text())
            print('Project Actions %s (%s)' % (manifest['version'], manifest['buildId']))
            return 0
        project = Path(arguments.project).expanduser().resolve()
        if arguments.command == 'add':
            project = attach_project(project)
            print('项目已接入：%s' % project)
            if not arguments.no_launch:
                open_desktop(project, arguments.project_id)
        elif arguments.command == 'open':
            actions.load_config(project)
            open_desktop(project, arguments.project_id)
        elif arguments.command == 'validate':
            config = actions.load_config(project)
            print('Valid: %s (%s actions)' % (config['project']['name'], len(config['actions'])))
        else:
            actions.load_config(project)
            os.execv(sys.executable, [sys.executable, str(TOOL / 'project_actions.py'),
                                     'serve', '--project', str(project), '--open'])
    except (actions.Problem, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
