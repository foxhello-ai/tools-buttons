"""Generic script-provided inputs and previews. No repository or business knowledge."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        raise Problem('参数必须是有效的 JSON 值')


def command(value):
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x and '\0' not in x for x in value):
        raise Problem('参数脚本命令必须是非空参数数组')


def options(value):
    if not isinstance(value, list) or len(value) > 1000:
        raise Problem('options 必须是最多 1000 项的数组')
    seen = set()
    for option in value:
        if not isinstance(option, dict) or not isinstance(option.get('label'), str) or not 0 < len(option['label']) <= 4096 or 'value' not in option:
            raise Problem('每个选项需要 label 和 value')
        encoded = canonical(option['value'])
        if len(encoded.encode("utf-8")) > 16384 or encoded in seen:
            raise Problem('选项值过长或重复')
        seen.add(encoded)
        if 'disabledReason' in option and (not isinstance(option['disabledReason'], str) or not option['disabledReason']):
            raise Problem('disabledReason 必须是非空字符串')
        summary(option.get('details', []))
    return value


def summary(value):
    if not isinstance(value, list) or len(value) > 128:
        raise Problem('summary/details 必须是最多 128 项的键值数组')
    for item in value:
        if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and len(item[k]) <= 16384 for k in ['label', 'value']):
            raise Problem('摘要项目需要字符串 label 和 value')
    return value


def validate(action):
    fields = action.get('parameters', [])
    if not isinstance(fields, list) or len(fields) > 64:
        raise Problem('parameters 必须是最多 64 项的数组')
    seen = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) - {'name','label','type','required','default','options','optionsCommand'}:
            raise Problem('参数字段无效或包含未知属性')
        name = field.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name) or name in seen:
            raise Problem('参数名必须是唯一的有效 ID')
        seen.add(name)
        if not isinstance(field.get('label'), str) or not 0 < len(field['label']) <= 256:
            raise Problem('参数需要 label')
        if not isinstance(field.get('type'), str) or field['type'] not in {'select','text','number','boolean'} or type(field.get('required', True)) is not bool:
            raise Problem('参数支持 select/text/number/boolean；required 必须是布尔值')
        if field['type'] == 'select':
            if ('options' in field) == ('optionsCommand' in field):
                raise Problem('select 必须且只能提供 options 或 optionsCommand')
            if 'optionsCommand' in field: command(field['optionsCommand'])
            else: options(field['options'])
        elif 'options' in field or 'optionsCommand' in field:
            raise Problem('只有 select 可声明选项')
        if 'default' in field: canonical(field['default'])
    if 'previewCommand' in action: command(action['previewCommand'])
    if 'resume' in action:
        resume = action['resume']
        if (not isinstance(resume, dict) or set(resume) != {'nodes'} or not isinstance(resume['nodes'], list)
                or not resume['nodes']
                or not all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', x) for x in resume['nodes']) or len(set(resume['nodes'])) != len(resume['nodes'])):
            raise Problem('resume.nodes 必须是非空且不重复的节点 ID 数组')
        if 'previewCommand' not in action:
            raise Problem('启用续跑必须配置 previewCommand')
    timeout = action.get('inputTimeout', 30)
    if type(timeout) not in (int, float) or not 0 < timeout <= 120:
        raise Problem('inputTimeout 必须在 0–120 秒之间')


def required_preview(action):
    return bool(action.get('parameters')) or 'previewCommand' in action


def input_environment(project, action, parameters_file, context_file):
    env = {k: v for k, v in os.environ.items() if not k.startswith('PROJECT_ACTIONS_')}
    env.update({k: v for k, v in action.get('env', {}).items() if not k.startswith('PROJECT_ACTIONS_')})
    env.update(PROJECT_ACTIONS_PROJECT_DIR=str(project), PROJECT_ACTIONS_PARAMETERS_FILE=str(parameters_file),
               PROJECT_ACTIONS_CONTEXT_FILE=str(context_file), PROJECT_ACTIONS_PYTHON=sys.executable, PYTHONUNBUFFERED='1')
    return env


def probe(project, action, args, parameters, context):
    """Bounded read-only-by-contract script; never supplies task event credentials."""
    with tempfile.TemporaryDirectory(prefix='project-actions-preview-') as temporary:
        directory = Path(temporary)
        parameters_file, context_file = directory/'parameters.json', directory/'context.json'
        parameters_file.write_text(canonical(parameters), encoding='utf-8')
        context_file.write_text(canonical(context), encoding='utf-8')
        cwd = Path(action.get('cwd', '.'))
        if not cwd.is_absolute(): cwd = project / cwd
        with (directory/'stdout').open('w+b') as output, (directory/'stderr').open('w+b') as error:
            kwargs = {'start_new_session': True} if os.name == 'posix' else {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
            try:
                process = subprocess.Popen(args, cwd=cwd, env=input_environment(project,action,parameters_file,context_file),
                                           stdin=subprocess.DEVNULL, stdout=output, stderr=error, **kwargs)
            except OSError as exc:
                raise Problem('无法启动参数脚本：%s' % exc)
            deadline = time.monotonic()+action.get('inputTimeout',30)
            try:
                while process.poll() is None:
                    if time.monotonic() >= deadline: raise Problem('参数/预览脚本超时', 408)
                    if os.fstat(output.fileno()).st_size + os.fstat(error.fileno()).st_size > 1024*1024:
                        raise Problem('参数脚本输出超过 1 MiB')
                    time.sleep(.02)
                if os.fstat(output.fileno()).st_size + os.fstat(error.fileno()).st_size > 1024*1024:
                    raise Problem('参数脚本输出超过 1 MiB')
                error.seek(0)
                if process.returncode:
                    raise Problem('参数/预览脚本失败（%s）：%s' % (process.returncode,error.read(8192).decode('utf-8',errors='replace')))
                output.seek(0)
                value = json.load(output)
                if not isinstance(value,dict):raise Problem('参数脚本必须返回一个 JSON 对象')
                canonical(value)
                return value
            except (ValueError, UnicodeError) as exc:
                raise Problem('参数脚本返回了无效 JSON：%s' % exc)
            finally:
                # Also close background descendants left by a completed probe.
                if os.name == 'posix':
                    try: os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        # A just-exited process group can already be inaccessible
                        # on macOS. Reap the child without masking the probe error.
                        if process.poll() is None:
                            try: process.kill()
                            except OSError: pass
                elif process.poll() is None:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    raise Problem('参数脚本未能停止', 409)


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def verify_checkpoint(project, checkpoint):
    root = (project/'.project-actions/runs'/checkpoint['runId']).resolve()
    path = Path(checkpoint['path']).resolve()
    if root not in path.parents or not path.is_file() or path.stat().st_size > 16*1024*1024:
        raise Problem('检查点已丢失或路径无效：%s' % checkpoint['nodeId'],409)
    if file_digest(path) != checkpoint['sha256']:
        raise Problem('检查点已变化，请重新生成或选择其他节点：%s' % checkpoint['nodeId'],409)


class Inputs:
    def __init__(self, project):
        self.project = project
        self.lock = threading.RLock()
        self.choices = {}
        self.previews = {}

    def remember(self, cache, value):
        with self.lock:
            expired = [key for key,item in cache.items() if item['expires'] < time.monotonic()]
            for key in expired:cache.pop(key)
            if len(cache) >= 100:raise Problem('待确认请求过多，请稍后重试',429)
            receipt=uuid.uuid4().hex
            cache[receipt]=dict(copy.deepcopy(value),expires=time.monotonic()+600)
            return receipt

    def options(self, action):
        fields=copy.deepcopy(action.get('parameters',[]))
        for field in fields:
            args=field.pop('optionsCommand',None)
            if args:
                data=probe(self.project,action,args,{},dict(version=1,mode='new',phase='options',parameter=field['name']))
                field['options']=options(data.get('options'))
                if 'default' in data:field['default']=data['default']
        receipt=self.remember(self.choices,dict(action=action,fields=fields))
        return dict(optionsId=receipt,parameters=fields)

    def parameters(self, action, values, receipt):
        if not isinstance(values,dict) or len(canonical(values).encode("utf-8")) > 32768:raise Problem('parameters 必须是最多 32 KiB 的对象')
        fields=action.get('parameters',[])
        if fields:
            with self.lock:item=copy.deepcopy(self.choices.get(receipt)) if isinstance(receipt,str) else None
            if item is None or item['expires'] < time.monotonic() or item['action'] != action:
                raise Problem('参数选项已过期或配置已改变，请刷新',409)
            fields=item['fields']
        if set(values)-{x['name'] for x in fields}:raise Problem('包含未声明参数')
        for field in fields:
            name=field['name']
            if name not in values:
                if field.get('required',True):raise Problem('缺少参数：'+field['label'])
                continue
            value=values[name]
            kind=field['type']
            if kind=='select':
                match=next((x for x in field['options'] if canonical(x['value'])==canonical(value)),None)
                if match is None or match.get('disabledReason'):raise Problem('选项不可用：'+field['label'])
            elif kind=='text' and (not isinstance(value,str) or len(value)>16384 or (field.get('required',True) and not value)):
                raise Problem('文本参数无效：'+field['label'])
            elif kind=='number' and type(value) not in (int,float):raise Problem('数字参数无效：'+field['label'])
            elif kind=='boolean' and type(value) is not bool:raise Problem('布尔参数无效：'+field['label'])
        return copy.deepcopy(values)

    def preview(self, action, parameters, context):
        data=probe(self.project,action,action['previewCommand'],parameters,dict(context,phase='preview')) if 'previewCommand' in action else dict(allowed=True,summary=[dict(label=k,value=canonical(v)) for k,v in parameters.items()])
        if type(data.get('allowed')) is not bool:raise Problem('预览需要 allowed 布尔值')
        summary(data.get('summary',[]))
        if not data['allowed']:
            if not isinstance(data.get('reason'),str) or not data['reason']:raise Problem('拒绝预览需提供 reason')
            return data
        plan=data.get('plan',[])
        if not isinstance(plan,list) or len(plan)>256:raise Problem('plan 必须是最多 256 项的数组')
        allowed_nodes={x['id'] for x in action.get('workflow',{}).get('nodes',[])}|set(context.get('nodeIds',[]))
        checkpoints={x['id']:x for x in context.get('resume',{}).get('checkpoints',[])}
        seen=set()
        for item in plan:
            if (not isinstance(item,dict) or not isinstance(item.get('nodeId'),str)
                    or item['nodeId'] not in allowed_nodes or item['nodeId'] in seen
                    or not isinstance(item.get('operation'),str) or item['operation'] not in {'run','reuse','skip'}):
                raise Problem('预览计划含无效或重复节点/操作')
            seen.add(item['nodeId'])
            if item['operation']=='reuse':
                checkpoint=checkpoints.get(item.get('checkpointId')) if isinstance(item.get('checkpointId'),str) else None
                if checkpoint is None or checkpoint['nodeId'] != item['nodeId']:raise Problem('复用节点必须引用同节点的已注册检查点')
                verify_checkpoint(self.project,checkpoint)
        if context['mode']=='resume' and not any(x['nodeId']==context['resume']['fromNode'] and x['operation']=='run' for x in plan):
            raise Problem('续跑计划必须执行所选起点')
        data['plan']=plan
        final_context=copy.deepcopy(context)
        final_context['plan']=plan
        receipt=self.remember(self.previews,dict(action=action,parameters=parameters,context=final_context,preview=data))
        return dict(data,previewId=receipt,parameters=parameters,context=final_context)

    def consume(self, action, receipt):
        with self.lock:item=self.previews.pop(receipt,None) if isinstance(receipt,str) else None
        if item is None or item['expires'] < time.monotonic():raise Problem('预览已过期或已使用，请重新确认',409)
        if item['action'] != action:raise Problem('动作配置已变化，请重新预览',409)
        available={x['id']:x for x in item['context'].get('resume',{}).get('checkpoints',[])}
        for step in item['context'].get('plan',[]):
            if step['operation']=='reuse':verify_checkpoint(self.project,available[step['checkpointId']])
        return item
