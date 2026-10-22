#!/usr/bin/env python3
"""Project Actions: local, configured command buttons with live task events."""

import argparse
import codecs
import copy
import hmac
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
import webbrowser

from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
# Bundled entry points also run with Python -I (which omits the script directory).
sys.path.insert(0, str(ROOT))
from action_inputs import (Problem, Inputs, validate as validate_inputs, required_preview,
                           verify_checkpoint, file_digest, canonical)
CONFIG_PATH = Path('.project-actions/actions.json')
IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
NODE_STATES = {'pending', 'running', 'succeeded', 'failed', 'skipped', 'cancelled'}
TERMINAL = {'succeeded', 'failed', 'cancelled', 'timed_out', 'interrupted'}
MAX_EVENTS = 2000


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def valid_id(value, label='ID'):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise Problem('%s must contain 1–64 letters, numbers, dots, underscores or hyphens' % label)
    return value


def require_string(value, label, limit=16384):
    if not isinstance(value, str) or not value or len(value) > limit or '\x00' in value:
        raise Problem('%s must be a nonempty string (at most %s characters)' % (label, limit))
    return value


def check_graph(nodes):
    visiting, visited = set(), set()

    def visit(node_id):
        if node_id in visiting:
            raise Problem('Task graph contains a cycle involving %s' % node_id)
        if node_id in visited:
            return
        visiting.add(node_id)
        for dependency in nodes[node_id]['dependsOn']:
            if dependency not in nodes:
                raise Problem('Unknown dependency: %s' % dependency)
            visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        visit(node_id)


def load_config(project_dir):
    path = project_dir / CONFIG_PATH
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise Problem('Cannot load %s: %s' % (path, error))
    if not isinstance(config, dict) or type(config.get('version')) is not int or config.get('version') != 1:
        raise Problem('Configuration version must be 1')
    project = config.get('project', {})
    if not isinstance(project, dict):
        raise Problem('project must be an object')
    default_id = 'project-' + hashlib.sha256(str(project_dir).encode('utf-8')).hexdigest()[:12]
    project['id'] = valid_id(project.get('id', default_id), 'Project ID')
    project['name'] = require_string(project.get('name', project_dir.name), 'Project name', 128)
    config['project'] = project
    actions = config.get('actions')
    if not isinstance(actions, list) or not actions:
        raise Problem('actions must be a nonempty array')
    ids = set()
    for action in actions:
        if not isinstance(action, dict):
            raise Problem('Each action must be an object')
        action_id = valid_id(action.get('id'), 'Action ID')
        if action_id in ids:
            raise Problem('Duplicate action ID: %s' % action_id)
        ids.add(action_id)
        require_string(action.get('tooltip'), 'Action tooltip', 256)
        require_string(action.get('icon', 'play'), 'Action icon', 32)
        command = action.get('command')
        if not isinstance(command, list) or not command:
            raise Problem('command must be a nonempty argument array')
        for argument in command:
            require_string(argument, 'Command argument')
        cwd = action.get('cwd', '.')
        require_string(cwd, 'Working directory')
        timeout = action.get('timeout', 300)
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout <= 86400:
            raise Problem('timeout must be between 0 and 86400 seconds')
        env = action.get('env', {})
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) and '\x00' not in k + v and '=' not in k for k, v in env.items()):
            raise Problem('env must be an object containing string environment variables')
        validate_inputs(action)
        workflow = action.get('workflow', {})
        if not isinstance(workflow, dict) or not isinstance(workflow.get('nodes', []), list):
            raise Problem('workflow.nodes must be an array')
        nodes = {}
        for node in workflow.get('nodes', []):
            if not isinstance(node, dict):
                raise Problem('Each workflow node must be an object')
            node_id = valid_id(node.get('id'), 'Node ID')
            if node_id in nodes:
                raise Problem('Duplicate node ID: %s' % node_id)
            require_string(node.get('label', node_id), 'Node label', 128)
            dependencies = node.get('dependsOn', [])
            if not isinstance(dependencies, list):
                raise Problem('dependsOn must be an array')
            for dependency in dependencies:
                valid_id(dependency, 'Dependency ID')
            nodes[node_id] = dict(node, dependsOn=list(dict.fromkeys(dependencies)))
        check_graph(nodes)
    return config


def initial_nodes(action):
    return {node['id']: dict(node, label=node.get('label', node['id']),
                            dependsOn=node.get('dependsOn', []), state='pending', progress=None)
            for node in action.get('workflow', {}).get('nodes', [])}


class Run:
    def __init__(self, run_dir, action, prepared=None):
        self.id = run_dir.name
        self.directory = run_dir
        self.action = copy.deepcopy(action)
        prepared = prepared or dict(parameters={}, context=dict(version=1,mode='new',plan=[]), preview={})
        self.parameters = copy.deepcopy(prepared['parameters'])
        self.context = copy.deepcopy(prepared['context'])
        self.context.update(runId=self.id, phase='run')
        self.preview = copy.deepcopy(prepared['preview'])
        self.checkpoints = []
        available = {x['id']:x for x in self.context.get('resume',{}).get('checkpoints',[])}
        self.inherited_checkpoints = [copy.deepcopy(available[x['checkpointId']]) for x in self.context.get('plan',[]) if x['operation']=='reuse']
        self.result = None
        self.action_id = action['id']
        self.title = action['tooltip']
        self.status = 'queued'
        self.created_at = now()
        self.finished_at = None
        self.exit_code = None
        self.reason = None
        self.nodes = initial_nodes(action)
        self.artifacts = []
        self.events = deque(maxlen=MAX_EVENTS)
        self.sequence = 0
        self.token = secrets.token_urlsafe(32)
        self.condition = threading.Condition(threading.RLock())
        self.process = None
        self.cancel_requested = False
        self.directory.mkdir(parents=True, exist_ok=False)
        (self.directory/'parameters.json').write_text(canonical(self.parameters),encoding='utf-8')
        (self.directory/'context.json').write_text(canonical(self.context),encoding='utf-8')
        self.event_file = (self.directory / 'events.jsonl').open('a', encoding='utf-8', buffering=1)
        self.publish('run', status='queued')

    def snapshot(self):
        with self.condition:
            return {'id': self.id, 'actionId': self.action_id, 'title': self.title,
                    'status': self.status, 'createdAt': self.created_at, 'finishedAt': self.finished_at,
                    'exitCode': self.exit_code, 'reason': self.reason,
                    'nodes': copy.deepcopy(list(self.nodes.values())), 'artifacts': copy.deepcopy(self.artifacts),
                    'sequence': self.sequence, 'action': copy.deepcopy(self.action),
                    'parameters': copy.deepcopy(self.parameters), 'context': copy.deepcopy(self.context),
                    'preview': copy.deepcopy(self.preview), 'checkpoints': copy.deepcopy(self.checkpoints),
                    'inheritedCheckpoints': copy.deepcopy(self.inherited_checkpoints), 'result': copy.deepcopy(self.result),
                    'parentRunId': self.context.get('resume',{}).get('parentRunId'),
                    'rootRunId': self.context.get('resume',{}).get('rootRunId', self.id),
                    'resumeFromNode': self.context.get('resume',{}).get('fromNode')}

    def persist(self):
        path = self.directory / 'run.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.snapshot(), ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def publish(self, event_type, **payload):
        with self.condition:
            self.sequence += 1
            event = dict(payload, type=event_type, runId=self.id, sequence=self.sequence, timestamp=now())
            self.events.append(event)
            self.event_file.write(json.dumps(event, ensure_ascii=False) + '\n')
            if event_type != 'log':
                self.persist()
            self.condition.notify_all()
            return event


class Runner:
    def __init__(self, project_dir):
        self.project_dir = project_dir.resolve()
        self.run_root = self.project_dir / '.project-actions/runs'
        self.runs = {}
        self.history = {}
        self.lock = threading.RLock()
        self.base_url = None
        self.shutting_down = False
        load_config(self.project_dir)
        self.inputs = Inputs(self.project_dir)
        self.restore_history()

    def restore_history(self):
        if not self.run_root.exists():
            return
        paths = sorted(self.run_root.glob('*/run.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        for path in paths[:100]:
            try:
                item = json.loads(path.read_text(encoding='utf-8'))
                if item['id'] != path.parent.name:
                    continue
                if item['status'] not in TERMINAL:
                    item.update(status='interrupted', reason='服务上次退出时任务尚未完成', finishedAt=now())
                    for node in item.get('nodes', []):
                        if node.get('state') in {'pending', 'running'}:
                            node['state'] = 'cancelled'
                    path.write_text(json.dumps(item, ensure_ascii=False), encoding='utf-8')
                self.history[item['id']] = item
            except (OSError, ValueError, KeyError):
                continue

    def list_runs(self):
        with self.lock:
            items = list(self.history.values()) + [run.snapshot() for run in self.runs.values()]
        return sorted(items, key=lambda item: item['createdAt'], reverse=True)[:100]

    def get_snapshot(self, run_id):
        with self.lock:
            if run_id in self.runs:
                return self.runs[run_id].snapshot()
            if run_id in self.history:
                return copy.deepcopy(self.history[run_id])
        if re.fullmatch(r'[a-f0-9]{32}', run_id):
            path = self.run_root/run_id/'run.json'
            if self.run_root.resolve() in path.resolve().parents and path.is_file():
                item=json.loads(path.read_text(encoding='utf-8'))
                if item.get('id')==run_id and item.get('status') in TERMINAL:return item
        raise Problem('Task not found', 404)

    def action(self, action_id):
        config = load_config(self.project_dir)
        action = next((item for item in config['actions'] if item['id'] == action_id), None)
        if action is None:
            raise Problem('Action not found in project configuration', 404)
        return action

    def options(self, action_id):
        return self.inputs.options(self.action(action_id))

    def preview_action(self, action_id, parameters, options_id=None):
        action=self.action(action_id)
        values=self.inputs.parameters(action,parameters,options_id)
        return self.inputs.preview(action,values,dict(version=1,mode='new'))

    def preview_resume(self, run_id, node_id):
        parent=self.get_snapshot(run_id)
        if parent['status'] not in TERMINAL or parent['status']=='succeeded':
            raise Problem('请先等待任务停止；成功任务无需续跑',409)
        action=self.action(parent['actionId'])
        if node_id not in action.get('resume',{}).get('nodes',[]) or node_id not in {x['id'] for x in parent['nodes']}:
            raise Problem('该节点未启用续跑')
        if parent.get('action')!=action:raise Problem('原任务的动作配置已变化，请新建任务',409)
        checkpoints=parent.get('inheritedCheckpoints',[])+parent.get('checkpoints',[])
        if not checkpoints:raise Problem('该任务未保存可恢复结果',409)
        context=dict(version=1,mode='resume',nodeIds=[x['id'] for x in parent['nodes']],resume=dict(
            fromNode=node_id,parentRunId=run_id,rootRunId=parent.get('rootRunId',run_id),
            parentRunDirectory=str(self.run_root/run_id),checkpoints=checkpoints))
        return self.inputs.preview(action,copy.deepcopy(parent.get('parameters',{})),context)

    def start(self, action_id, preview_id=None):
        action=self.action(action_id)
        with self.lock:
            if self.shutting_down:
                raise Problem('Service is shutting down', 503)
            active = [run for run in self.runs.values() if run.status not in TERMINAL]
            if any(run.action_id == action_id for run in active):
                raise Problem('This action already has a running task', 409)
            if len(active) >= 4:
                raise Problem('At most four tasks can run at once', 409)
            prepared=None
            if preview_id is not None or required_preview(action):
                prepared=self.inputs.consume(action,preview_id)
                parent_id=prepared['context'].get('resume',{}).get('parentRunId')
                if parent_id and self.get_snapshot(parent_id)['status'] not in TERMINAL:
                    raise Problem('原任务尚未停止',409)
            run = Run(self.run_root / uuid.uuid4().hex, action, prepared)
            self.runs[run.id] = run
        threading.Thread(target=self.execute, args=(run,), daemon=True, name='action-' + run.id[:8]).start()
        return run

    def stop_process(self, run):
        process = run.process
        if process is None:
            return
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGTERM)
            elif process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True, timeout=5)
        except (ProcessLookupError, OSError, subprocess.SubprocessError):
            pass

    def kill_process(self, run):
        process = run.process
        if process is None:
            return
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            elif process.poll() is None:
                process.kill()
        except (ProcessLookupError, OSError):
            pass

    def cancel(self, run_id):
        with self.lock:
            run = self.runs.get(run_id)
            if run is None:
                raise Problem('Active task not found', 404)
        with run.condition:
            if run.status in TERMINAL:
                return run.snapshot()
            run.cancel_requested = True
            run.publish('log', level='warning', message='正在停止任务…', source='service')
            self.stop_process(run)
        return run.snapshot()

    def execute(self, run):
        readers = []
        try:
            with run.condition:
                if run.cancel_requested:
                    self.finish(run, 'cancelled', None, '任务在启动前被取消')
                    return
                cwd = Path(run.action.get('cwd', '.'))
                if not cwd.is_absolute():
                    cwd = self.project_dir / cwd
                env = os.environ.copy()
                env.update(run.action.get('env', {}))
                env.update({'PROJECT_ACTIONS_PROJECT_DIR': str(self.project_dir),
                            'PROJECT_ACTIONS_PARAMETERS_FILE': str(run.directory/'parameters.json'),
                            'PROJECT_ACTIONS_CONTEXT_FILE': str(run.directory/'context.json'),
                            'PROJECT_ACTIONS_RUN_ID': run.id,
                            'PROJECT_ACTIONS_RUN_DIR': str(run.directory),
                            'PROJECT_ACTIONS_EVENT_URL': self.base_url + '/api/runs/' + run.id + '/report',
                            'PROJECT_ACTIONS_EVENT_TOKEN': run.token,
                            'PROJECT_ACTIONS_CLI': str(Path(__file__).resolve()),
                            'PROJECT_ACTIONS_PYTHON': sys.executable,
                            'PROJECT_ACTIONS_SHELL': str(ROOT / 'shell/events.sh'),
                            'PYTHONUNBUFFERED': '1'})
                for checkpoint in run.inherited_checkpoints:verify_checkpoint(self.project_dir,checkpoint)
                if run.context['mode']=='resume':
                    run.publish('log',level='info',source='service',message='从节点 %s 继续；原任务 %s' % (run.context['resume']['fromNode'],run.context['resume']['parentRunId']))
                kwargs = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                run.process = subprocess.Popen(run.action['command'], cwd=cwd, env=env,
                                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
                run.status = 'running'
                run.publish('run', status='running')
            for source, pipe in [('stdout', run.process.stdout), ('stderr', run.process.stderr)]:
                reader = threading.Thread(target=self.capture, args=(run, pipe, source), daemon=True)
                reader.start()
                readers.append(reader)
            deadline = time.monotonic() + run.action.get('timeout', 300)
            cancelled_at = None
            status, reason = None, None
            # A script can exit while its child still owns stdout/stderr. Keep the
            # task and its timeout active until both the process and output finish.
            while run.process.poll() is None or any(reader.is_alive() for reader in readers):
                if run.cancel_requested or time.monotonic() >= deadline:
                    if cancelled_at is None:
                        status = 'cancelled' if run.cancel_requested else 'timed_out'
                        reason = '任务已取消' if run.cancel_requested else '任务超过配置的超时时间'
                        self.stop_process(run)
                        cancelled_at = time.monotonic()
                    if time.monotonic() - cancelled_at >= 2:
                        self.kill_process(run)
                    if time.monotonic() - cancelled_at >= 4:
                        break
                time.sleep(0.05)
            for reader in readers:
                reader.join(timeout=1)
            if status is None:
                if run.cancel_requested:
                    status, reason = 'cancelled', '任务已取消'
                else:
                    status = 'succeeded' if run.process.returncode == 0 else 'failed'
                    reason = None if status == 'succeeded' else '命令退出码：%s' % run.process.returncode
                if status == 'succeeded' and any(node['state'] == 'failed' for node in run.nodes.values()):
                    status, reason = 'failed', '脚本上报了失败节点'
            if status == 'succeeded' and run.context.get('plan'):
                for step in run.context['plan']:
                    node=run.nodes.get(step['nodeId'],{})
                    if step['operation']!='skip' and (node.get('state')!='succeeded' or (step['operation']=='reuse' and node.get('execution')!='reused')):
                        status,reason='failed','脚本未确认计划节点结果：'+step['nodeId']
                        break
            self.finish(run, status, run.process.returncode, reason)
        except Exception as error:
            self.stop_process(run)
            self.finish(run, 'failed', None, str(error))

    def capture(self, run, pipe, source):
        decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
        pending = ''
        try:
            while True:
                chunk = os.read(pipe.fileno(), 4096)
                if not chunk:
                    pending += decoder.decode(b'', final=True)
                    break
                pending += decoder.decode(chunk)
                while '\n' in pending or len(pending) >= 16384:
                    split = pending.find('\n')
                    width = min(split + 1 if split >= 0 else len(pending), 16384)
                    line, pending = pending[:width].rstrip('\r\n'), pending[width:]
                    with run.condition:
                        if run.status not in TERMINAL:
                            run.publish('log', level='error' if source == 'stderr' else 'info', message=line, source=source)
            if pending:
                with run.condition:
                    if run.status not in TERMINAL:
                        run.publish('log', level='error' if source == 'stderr' else 'info', message=pending, source=source)
        finally:
            pipe.close()

    def finish(self, run, status, exit_code, reason):
        with run.condition:
            if run.status in TERMINAL:
                return
            for node in run.nodes.values():
                if node['state'] in {'pending', 'running'}:
                    state = ('succeeded' if node['state'] == 'running' else 'skipped') if status == 'succeeded' else (
                        'failed' if status in {'failed', 'timed_out'} and node['state'] == 'running' else
                        'cancelled' if status in {'cancelled', 'timed_out'} else 'skipped')
                    node['state'] = state
                    run.publish('node', node=copy.deepcopy(node))
            run.status, run.exit_code, run.reason, run.finished_at = status, exit_code, reason, now()
            run.publish('run', status=status, exitCode=exit_code, reason=reason, finishedAt=run.finished_at)
            run.event_file.close()

    def report(self, run_id, token, payload):
        with self.lock:
            run = self.runs.get(run_id)
        if run is None or not hmac.compare_digest(token, run.token):
            raise Problem('Invalid task event token', 403)
        if not isinstance(payload, dict):
            raise Problem('Event must be a JSON object')
        with run.condition:
            if run.status in TERMINAL:
                raise Problem('Task has already finished', 409)
            event_type = payload.get('type')
            if not isinstance(event_type, str):
                raise Problem('Event type must be a string')
            if event_type == 'log':
                level = payload.get('level', 'info')
                if not isinstance(level, str) or level not in {'debug', 'info', 'warning', 'error'}:
                    raise Problem('Unknown log level')
                message = require_string(payload.get('message'), 'Log message')
                node_id = payload.get('nodeId')
                if node_id is not None:
                    valid_id(node_id, 'Node ID')
                return run.publish('log', level=level, message=message, nodeId=node_id, source='script')
            if event_type in {'node', 'progress'}:
                node_id = valid_id(payload.get('id'), 'Node ID')
                nodes = copy.deepcopy(run.nodes)
                node = nodes.setdefault(node_id, {'id': node_id, 'label': node_id, 'dependsOn': [], 'state': 'pending', 'progress': None})
                if event_type == 'node':
                    state = payload.get('state')
                    if not isinstance(state, str) or state not in NODE_STATES:
                        raise Problem('Unknown node state')
                    node['state'] = state
                    if state == 'running':
                        node.pop('execution',None);node.pop('reusedFrom',None)
                    if payload.get('execution') == 'reused':
                        step=next((x for x in run.context.get('plan',[]) if x['nodeId']==node_id and x['operation']=='reuse' and x['checkpointId']==payload.get('checkpointId')),None)
                        checkpoint=next((x for x in run.inherited_checkpoints if x['id']==payload.get('checkpointId')),None)
                        if state!='succeeded' or step is None or checkpoint is None:raise Problem('复用事件必须对应已确认计划和检查点')
                        verify_checkpoint(self.project_dir,checkpoint)
                        node.update(execution='reused',reusedFrom=dict(runId=checkpoint['runId'],checkpointId=checkpoint['id']))
                    elif 'execution' in payload:raise Problem('execution 只支持 reused')
                    if 'label' in payload:
                        node['label'] = require_string(payload['label'], 'Node label', 128)
                    if 'dependsOn' in payload:
                        dependencies = payload['dependsOn']
                        if not isinstance(dependencies, list):
                            raise Problem('dependsOn must be an array')
                        node['dependsOn'] = list(dict.fromkeys(valid_id(item, 'Dependency ID') for item in dependencies))
                        for dependency in node['dependsOn']:
                            nodes.setdefault(dependency, {'id': dependency, 'label': dependency, 'dependsOn': [], 'state': 'pending', 'progress': None})
                    if state == 'succeeded':
                        node['progress'] = 100
                else:
                    completed, total = payload.get('completed'), payload.get('total')
                    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in [completed, total]) or not 0 <= completed <= total or total <= 0:
                        raise Problem('Progress requires 0 <= completed <= total and total > 0')
                    node.update(progress=round(completed / total * 100, 1))
                if 'message' in payload:
                    node['message'] = require_string(payload['message'], 'Node message', 1024)
                check_graph(nodes)
                run.nodes = nodes
                return run.publish('node', node=copy.deepcopy(node), nodes=copy.deepcopy(list(nodes.values())))
            if event_type == 'checkpoint':
                node_id=valid_id(payload.get('nodeId'),'Node ID')
                if node_id not in run.nodes:raise Problem('检查点必须属于已声明或已上报节点')
                path=Path(require_string(payload.get('path'),'Checkpoint path'))
                path=(run.directory/path).resolve() if not path.is_absolute() else path.resolve()
                if run.directory.resolve() not in path.parents or not path.is_file() or path.stat().st_size>16*1024*1024:
                    raise Problem('检查点必须是本次任务目录内最多 16 MiB 的文件')
                checkpoint=dict(id=uuid.uuid4().hex,nodeId=node_id,runId=run.id,path=str(path),sha256=file_digest(path),size=path.stat().st_size,
                                label=require_string(payload.get('label',path.name),'Checkpoint label',256))
                run.checkpoints=[x for x in run.checkpoints if x['path']!=str(path)]+[checkpoint]
                return run.publish('checkpoint',checkpoint=checkpoint)
            if event_type == 'result':
                data=payload.get('data')
                if not isinstance(data,dict) or len(canonical(data).encode("utf-8"))>32768:raise Problem('result.data 必须是最多 32 KiB 的 JSON 对象')
                run.result=copy.deepcopy(data)
                return run.publish('result',data=data)
            if event_type == 'artifact':
                raw = require_string(payload.get('path'), 'Artifact path')
                path = Path(raw)
                path = (self.project_dir / path).resolve() if not path.is_absolute() else path.resolve()
                if not (path == self.project_dir or self.project_dir in path.parents) or not path.is_file():
                    raise Problem('Artifact must be an existing file inside this project')
                artifact = {'id': uuid.uuid4().hex, 'label': payload.get('label') or path.name,
                            'path': str(path.relative_to(self.project_dir)), 'size': path.stat().st_size}
                require_string(artifact['label'], 'Artifact label', 256)
                run.artifacts.append(artifact)
                return run.publish('artifact', artifact=artifact)
            raise Problem('Unknown event type; expected log, node, progress, artifact, checkpoint or result')

    def events_after(self, run_id, after):
        with self.lock:
            run = self.runs.get(run_id)
        if run is None:
            snapshot = self.get_snapshot(run_id)
            path = self.run_root / run_id / 'events.jsonl'
            events = deque(maxlen=MAX_EVENTS)
            if path.is_file():
                with path.open(encoding='utf-8') as stream:
                    for line in stream:
                        try:
                            event = json.loads(line)
                            if event['sequence'] > after:
                                events.append(event)
                        except (ValueError, KeyError):
                            continue
            return list(events), True, snapshot
        with run.condition:
            events = [event for event in run.events if event['sequence'] > after]
            done = run.status in TERMINAL
            if not events and not done:
                run.condition.wait(timeout=10)
                events = [event for event in run.events if event['sequence'] > after]
                done = run.status in TERMINAL
            return copy.deepcopy(events), done, run.snapshot()

    def shutdown(self):
        with self.lock:
            self.shutting_down = True
            active = [run for run in self.runs.values() if run.status not in TERMINAL]
        for run in active:
            self.cancel(run.id)
        deadline = time.monotonic() + 4
        while any(run.status not in TERMINAL for run in active) and time.monotonic() < deadline:
            time.sleep(0.05)
        for run in active:
            if run.status not in TERMINAL:
                self.kill_process(run)


class ActionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, runner):
        super().__init__(address, Handler)
        self.runner = runner
        self.token = secrets.token_urlsafe(32)
        self.runner.base_url = 'http://127.0.0.1:%s' % self.server_port


class ServiceLease:
    """One CLI service per project; publish credentials only to the local user."""
    def __init__(self, project_dir):
        self.directory = project_dir.resolve() / '.project-actions'
        self.stream = None
        self.runtime = self.directory / '.runtime.json'

    def __enter__(self):
        load_config(self.directory.parent)
        descriptor = os.open(str(self.directory / '.runtime.lock'), os.O_CREAT | os.O_RDWR, 0o600)
        self.stream = os.fdopen(descriptor, 'r+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise Problem('This project already has a running service; use its .project-actions/.runtime.json URL')
        return self

    def publish(self, server):
        payload = {'version': 1, 'pid': os.getpid(), 'url': server.runner.base_url,
                   'directory': str(server.runner.project_dir), 'token': server.token}
        temporary = self.runtime.with_name('.runtime.%s.tmp' % uuid.uuid4().hex)
        try:
            with os.fdopen(os.open(str(temporary), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'w') as stream:
                json.dump(payload, stream)
            os.replace(str(temporary), str(self.runtime))
        finally:
            temporary.unlink(missing_ok=True)

    def __exit__(self, *args):
        try:
            self.runtime.unlink(missing_ok=True)
        finally:
            if self.stream:
                self.stream.close()


class Handler(BaseHTTPRequestHandler):
    server_version = 'ProjectActions/1'

    def log_message(self, format_string, *args):
        if self.path.startswith('/api/'):
            return
        super().log_message(format_string, *args)

    def headers_for(self, status, content_type, length=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if length is not None:
            self.send_header('Content-Length', str(length))
        self.end_headers()

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.headers_for(status, 'application/json; charset=utf-8', len(body))
        self.wfile.write(body)

    def check_source(self):
        host = self.headers.get('Host', '')
        if host not in {'127.0.0.1:%s' % self.server.server_port, 'localhost:%s' % self.server.server_port}:
            raise Problem('Unrecognized host', 403)
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + host:
            raise Problem('Cross-origin requests are not allowed', 403)

    def authorize(self):
        supplied = self.headers.get('Authorization', '')
        if not hmac.compare_digest(supplied, 'Bearer ' + self.server.token):
            raise Problem('Invalid interface token; reopen the project page', 403)

    def read_json(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise Problem('Request must contain at most 64 KiB of JSON', 413)
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError) as error:
            raise Problem('Invalid JSON request: %s' % error)

    def do_GET(self):
        try:
            self.check_source()
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == '/favicon.ico':
                self.headers_for(204, 'image/x-icon', 0)
                return
            if path in {'/', '/app.js', '/style.css'}:
                file = ROOT / 'web' / ('index.html' if path == '/' else path[1:])
                body = file.read_bytes()
                if path == '/':
                    body = body.replace(b'__PROJECT_ACTIONS_TOKEN__', self.server.token.encode('ascii'))
                kind = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css'}[path]
                self.headers_for(200, kind + '; charset=utf-8', len(body))
                self.wfile.write(body)
                return
            self.authorize()
            options_match=re.fullmatch(r'/api/actions/([A-Za-z0-9_.-]+)/options',path)
            if options_match:
                self.send_json(200,self.server.runner.options(options_match.group(1)));return
            if path == '/api/project':
                config = load_config(self.server.runner.project_dir)
                self.send_json(200, {'project': config['project'], 'actions': config['actions'],
                                     'directory': str(self.server.runner.project_dir),
                                     'runs': self.server.runner.list_runs()})
                return
            match = re.fullmatch(r'/api/runs/([a-f0-9]{32})(?:/(events|artifacts)/?([a-f0-9]{32})?)?', path)
            if not match:
                raise Problem('Not found', 404)
            run_id, resource, artifact_id = match.groups()
            if resource == 'events':
                after = max(0, int(parse_qs(parsed.query).get('after', ['0'])[0]))
                self.stream_events(run_id, after)
            elif resource == 'artifacts':
                snapshot = self.server.runner.get_snapshot(run_id)
                artifact = next((item for item in snapshot['artifacts'] if item['id'] == artifact_id), None)
                if artifact is None:
                    raise Problem('Artifact not found', 404)
                file = (self.server.runner.project_dir / artifact['path']).resolve()
                if self.server.runner.project_dir not in file.parents or not file.is_file():
                    raise Problem('Artifact is no longer available', 404)
                self.headers_for(200, 'application/octet-stream', file.stat().st_size)
                with file.open('rb') as stream:
                    shutil.copyfileobj(stream, self.wfile, length=65536)
            else:
                self.send_json(200, self.server.runner.get_snapshot(run_id))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Problem as error:
            self.send_json(error.status, {'error': str(error)})
        except (OSError, ValueError) as error:
            self.send_json(400, {'error': str(error)})

    def stream_events(self, run_id, after):
        self.server.runner.get_snapshot(run_id)
        self.headers_for(200, 'text/event-stream; charset=utf-8')
        while True:
            events, done, snapshot = self.server.runner.events_after(run_id, after)
            if events and events[0]['sequence'] > after + 1:
                self.wfile.write(('event: snapshot\ndata: %s\n\n' % json.dumps(snapshot, ensure_ascii=False)).encode('utf-8'))
            for event in events:
                self.wfile.write(('id: %s\nevent: update\ndata: %s\n\n' % (event['sequence'], json.dumps(event, ensure_ascii=False))).encode('utf-8'))
                after = event['sequence']
            if not events:
                self.wfile.write(b': keepalive\n\n')
            self.wfile.flush()
            if done:
                break

    def do_POST(self):
        try:
            self.check_source()
            match = re.fullmatch(r'/api/runs/([a-f0-9]{32})/report', self.path)
            if match:
                authorization = self.headers.get('Authorization', '')
                token = authorization[7:] if authorization.startswith('Bearer ') else ''
                event = self.server.runner.report(match.group(1), token, self.read_json())
                self.send_json(200, {'sequence': event['sequence']})
                return
            self.authorize()
            preview_match=re.fullmatch(r'/api/actions/([A-Za-z0-9_.-]+)/preview',self.path)
            resume_match=re.fullmatch(r'/api/runs/([a-f0-9]{32})/resume-preview',self.path)
            if preview_match or resume_match:
                payload=self.read_json()
                if not isinstance(payload,dict):raise Problem('请求必须是 JSON 对象')
                if preview_match:
                    if set(payload)-{'parameters','optionsId'}:raise Problem('预览仅接受 parameters/optionsId')
                    data=self.server.runner.preview_action(preview_match.group(1),payload.get('parameters',{}),payload.get('optionsId'))
                else:
                    if set(payload)!={'nodeId'}:raise Problem('续跑预览仅接受 nodeId')
                    data=self.server.runner.preview_resume(resume_match.group(1),valid_id(payload['nodeId'],'Node ID'))
                self.send_json(200,data);return
            if self.path == '/api/runs':
                payload = self.read_json()
                if not isinstance(payload, dict) or 'actionId' not in payload or set(payload)-{'actionId','previewId'}:
                    raise Problem('Specify actionId and optional previewId; commands come from the project configuration')
                run = self.server.runner.start(valid_id(payload['actionId'], 'Action ID'),payload.get('previewId'))
                self.send_json(202, run.snapshot())
                return
            match = re.fullmatch(r'/api/runs/([a-f0-9]{32})/cancel', self.path)
            if match:
                self.send_json(200, self.server.runner.cancel(match.group(1)))
                return
            raise Problem('Not found', 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Problem as error:
            self.send_json(error.status, {'error': str(error)})
        except (OSError, ValueError) as error:
            self.send_json(400, {'error': str(error)})


def initialize(project_dir):
    project_dir.mkdir(parents=True, exist_ok=True)
    destination = project_dir / '.project-actions'
    if (destination / 'actions.json').exists():
        raise Problem('Project already has .project-actions/actions.json; existing files were preserved')
    destination.mkdir(exist_ok=True)
    scripts = destination / 'scripts'
    if scripts.exists():
        raise Problem('Project already has .project-actions/scripts; existing files were preserved')
    shutil.copytree(ROOT / 'templates/scripts', scripts)
    shutil.copy2(ROOT / 'schema.json', destination / 'schema.json')
    for name in ('AI_GUIDE.md', 'PROTOCOL.md', 'PARAMETERS_AND_RESUME.md'):
        shutil.copy2(ROOT / name, destination / name)
    shutil.copy2(ROOT / 'templates/AGENTS.md.template', destination / 'AGENTS.md')
    config = json.loads((ROOT / 'templates/actions.json').read_text(encoding='utf-8'))
    config['project'] = {'id': 'project-' + uuid.uuid4().hex[:12], 'name': project_dir.name}
    (destination / 'actions.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (destination / '.gitignore').write_text('runs/\n.runtime*\n', encoding='utf-8')
    print('Created %s' % (destination / 'actions.json'))


def emit(arguments):
    if arguments.kind == 'log':
        payload = {'type': 'log', 'level': arguments.level, 'message': arguments.message}
        if arguments.node:
            payload['nodeId'] = arguments.node
    elif arguments.kind == 'node':
        payload = {'type': 'node', 'id': arguments.id, 'state': arguments.state}
        if arguments.label:
            payload['label'] = arguments.label
        if arguments.after is not None:
            payload['dependsOn'] = arguments.after
        if arguments.message:
            payload['message'] = arguments.message
    elif arguments.kind == 'reuse':
        payload = {'type':'node','id':arguments.id,'state':'succeeded','execution':'reused','checkpointId':arguments.checkpoint}
    elif arguments.kind == 'checkpoint':
        payload = {'type':'checkpoint','nodeId':arguments.node,'path':arguments.path}
        if arguments.label:payload['label']=arguments.label
    elif arguments.kind == 'progress':
        payload = {'type': 'progress', 'id': arguments.id, 'completed': arguments.completed, 'total': arguments.total}
        if arguments.message:
            payload['message'] = arguments.message
    elif arguments.kind == 'artifact':
        payload = {'type': 'artifact', 'path': arguments.path}
        if arguments.label:
            payload['label'] = arguments.label
    else:
        payload = json.load(sys.stdin)
    endpoint = os.environ.get('PROJECT_ACTIONS_EVENT_URL')
    token = os.environ.get('PROJECT_ACTIONS_EVENT_TOKEN')
    if not endpoint or not token:
        raise Problem('Event reporting is available inside a task started by Project Actions')
    request = Request(endpoint, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), method='POST',
                      headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    try:
        with urlopen(request, timeout=10) as response:
            response.read()
    except HTTPError as error:
        raise Problem('Cannot report event: ' + error.read().decode('utf-8', errors='replace'))
    except URLError as error:
        raise Problem('Cannot report event: %s' % error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='Add a configuration and example scripts to a project')
    init.add_argument('directory', nargs='?', default='.')
    serve = commands.add_parser('serve', help='Open the configured project buttons')
    serve.add_argument('--project', default='.')
    serve.add_argument('--port', type=int, default=0)
    serve.add_argument('--open', action='store_true', help='Open the page in the default browser')
    validate = commands.add_parser('validate', help='Validate the project configuration')
    validate.add_argument('--project', default='.')
    example = commands.add_parser('example',help='Add the isolated parameter/resume demo')
    example.add_argument('--project',default='.')
    event = commands.add_parser('emit', help='Report a task event from a running script')
    kinds = event.add_subparsers(dest='kind', required=True)
    log = kinds.add_parser('log')
    log.add_argument('level', choices=['debug', 'info', 'warning', 'error'])
    log.add_argument('message')
    log.add_argument('--node')
    node = kinds.add_parser('node')
    node.add_argument('id')
    node.add_argument('state', choices=sorted(NODE_STATES))
    node.add_argument('--label')
    node.add_argument('--after', action='append')
    node.add_argument('--message')
    progress = kinds.add_parser('progress')
    progress.add_argument('id')
    progress.add_argument('completed', type=float)
    progress.add_argument('total', type=float)
    progress.add_argument('--message')
    artifact = kinds.add_parser('artifact')
    artifact.add_argument('path')
    artifact.add_argument('--label')
    checkpoint = kinds.add_parser('checkpoint', help='Register an opaque recovery checkpoint')
    checkpoint.add_argument('node')
    checkpoint.add_argument('path')
    checkpoint.add_argument('--label')
    reuse = kinds.add_parser('reuse', help='Confirm a validated reused node')
    reuse.add_argument('id')
    reuse.add_argument('checkpoint')
    kinds.add_parser('json', help='Read one event JSON object from standard input')
    arguments = parser.parse_args()
    try:
        if arguments.command == 'init':
            initialize(Path(arguments.directory).resolve())
        elif arguments.command == 'validate':
            config = load_config(Path(arguments.project).resolve())
            print('Valid: %s (%s actions)' % (config['project']['name'], len(config['actions'])))
        elif arguments.command == 'example':
            subprocess.run([sys.executable,str(ROOT/'examples/resumable-pipeline/install.py'),str(Path(arguments.project).resolve())],check=True)
        elif arguments.command == 'emit':
            emit(arguments)
        else:
            with ServiceLease(Path(arguments.project)) as lease:
                runner = Runner(Path(arguments.project))
                server = ActionServer(('127.0.0.1', arguments.port), runner)
                def stop(signum, frame):
                    threading.Thread(target=server.shutdown, daemon=True).start()
                signal.signal(signal.SIGTERM, stop)
                signal.signal(signal.SIGINT, stop)
                lease.publish(server)
                print('Project: %s\nURL: %s/' % (runner.project_dir, runner.base_url), flush=True)
                if arguments.open:
                    webbrowser.open(runner.base_url + '/')
                try:
                    server.serve_forever(poll_interval=0.1)
                finally:
                    runner.shutdown()
                    server.server_close()
    except Problem as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
