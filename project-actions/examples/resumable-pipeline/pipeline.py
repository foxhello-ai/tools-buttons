#!/usr/bin/env python3
"""Project-owned example: local files only. Replace these stages with your business."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, urlopen

STAGES = ['check','build','deploy','verify']
LABELS = dict(zip(STAGES,['检查','构建与测试','模拟部署','部署后检查']))
PROJECT = Path(os.environ['PROJECT_ACTIONS_PROJECT_DIR'])
DATA = PROJECT/'.project-actions/demo-data'


def read(path):return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)


def event(kind,**data):
    request=Request(os.environ['PROJECT_ACTIONS_EVENT_URL'],data=json.dumps(dict(type=kind,**data)).encode(),
                    headers={'Authorization':'Bearer '+os.environ['PROJECT_ACTIONS_EVENT_TOKEN'],'Content-Type':'application/json'})
    with urlopen(request,timeout=10) as response:return json.load(response)


def identity(source):
    """All Git/source decisions belong to this project script, outside the app."""
    directory=Path(source['directory']).resolve()
    if 'commit' in source:
        def git(*args):return subprocess.check_output(['git','-C',str(directory),*args])
        if git('rev-parse','HEAD').decode().strip()!=source['commit']:raise ValueError('提交已改变，请重新选择版本')
        paths=source.get('paths',['.'])
        if git('status','--porcelain','--untracked-files=all','--',*paths).strip():raise ValueError('源码存在未提交或未跟踪改动')
        files=sorted(x for x in git('ls-files','-z','--',*paths).split(b'\0') if x)
        digest=hashlib.sha256()
        for name in files:
            path=directory/os.fsdecode(name)
            if path.is_symlink() or not path.is_file():raise ValueError('示例不支持源码符号链接或子模块')
            digest.update(name+b'\0'+path.read_bytes()+b'\0')
        actual=digest.hexdigest()
    else:actual=sha(directory/'source.txt')
    if actual!=source['fingerprint']:raise ValueError('源码内容已改变，停止执行；请重新选择版本')
    return actual


def validate_checkpoint(checkpoint,parameters):
    data=read(checkpoint['path'])
    if data.get('version')!=1 or data['source']!=parameters['source'] or data['environment']!=parameters['environment']:
        raise ValueError('检查点版本或参数不匹配')
    if 'artifact' in data and (not Path(data['artifact']).is_file() or sha(data['artifact'])!=data['artifactSha256']):
        raise ValueError('构建产物缺失或变化，请从构建节点重新运行')
    if checkpoint['nodeId']=='deploy':
        live=read(DATA/'environment'/('current-'+parameters['environment']+'.json'))
        if live!=data['published']:raise ValueError('环境当前版本与检查点不同，不能只执行部署后检查')
    return data


def preview(parameters,context):
    identity(parameters['source'])
    delay=parameters['delay']
    if type(delay) not in (int,float) or not 0<=delay<=60:raise ValueError('模拟部署等待时间需在 0–60 秒之间')
    if parameters['environment'] not in ['sandbox','test']:raise ValueError('仅支持本地模拟环境')
    index=STAGES.index(context['resume']['fromNode']) if context['mode']=='resume' else 0
    checkpoints=context.get('resume',{}).get('checkpoints',[])
    plan=[]
    for i,node in enumerate(STAGES):
        if i<index:
            checkpoint=next((x for x in reversed(checkpoints) if x['nodeId']==node),None)
            if checkpoint is None:raise ValueError('缺少 %s 检查点，请选择更早的节点' % LABELS[node])
            validate_checkpoint(checkpoint,parameters)
            plan.append(dict(nodeId=node,operation='reuse',checkpointId=checkpoint['id']))
        else:plan.append(dict(nodeId=node,operation='run'))
    return dict(allowed=True,summary=[dict(label='源码目录',value=parameters['source']['directory']),
                dict(label='固定版本',value=parameters['source'].get('commit',parameters['source']['fingerprint'])),
                dict(label='环境',value=parameters['environment']+'（本地模拟）')],plan=plan)


def run(parameters,context):
    # Revalidate business state immediately before execution; preview can become stale.
    checked=preview(parameters,context)
    if checked['plan']!=context['plan']:raise ValueError('执行计划与确认结果不同')
    run_dir=Path(os.environ['PROJECT_ACTIONS_RUN_DIR'])
    checkpoints={x['id']:x for x in context.get('resume',{}).get('checkpoints',[])}
    artifact=None
    artifact_sha=None
    for step in context['plan']:
        node=step['nodeId']
        if step['operation']=='reuse':
            data=validate_checkpoint(checkpoints[step['checkpointId']],parameters)
            if 'artifact' in data:artifact,artifact_sha=Path(data['artifact']),data['artifactSha256']
            event('node',id=node,state='succeeded',execution='reused',checkpointId=step['checkpointId'])
            event('log',level='info',nodeId=node,message='复用已校验的结果，未重复执行该阶段')
            continue
        identity(parameters['source'])
        event('node',id=node,state='running')
        event('log',level='info',nodeId=node,message='开始：'+LABELS[node])
        data=dict(version=1,source=parameters['source'],environment=parameters['environment'])
        if node=='check':time.sleep(.15)
        elif node=='build':
            artifact=run_dir/'built.txt'
            artifact.write_text('模拟产物：'+parameters['source']['fingerprint'],encoding='utf-8')
            artifact_sha=sha(artifact)
            data.update(artifact=str(artifact),artifactSha256=artifact_sha)
            time.sleep(.3)
        elif node=='deploy':
            # This example uses an atomic local file to represent a deployment.
            # A real project must implement its own remote lock, baseline and recovery.
            for i in range(10):
                time.sleep(parameters['delay']/10)
                event('progress',id=node,completed=i+1,total=10,message='模拟部署中，可停止后从本节点继续')
            identity(parameters['source'])
            if sha(artifact)!=artifact_sha:raise ValueError('发布前产物已变化，停止发布；请重新构建')
            published=dict(source=parameters['source'],artifactSha256=artifact_sha)
            write(DATA/'environment'/('current-'+parameters['environment']+'.json'),published)
            data.update(artifact=str(artifact),artifactSha256=artifact_sha,published=published)
        elif node=='verify':
            live=read(DATA/'environment'/('current-'+parameters['environment']+'.json'))
            if sha(artifact)!=artifact_sha or live!=dict(source=parameters['source'],artifactSha256=artifact_sha):raise ValueError('模拟环境版本或产物不一致')
            data.update(artifact=str(artifact),artifactSha256=artifact_sha)
        identity(parameters['source'])
        checkpoint=run_dir/(node+'-checkpoint.json')
        write(checkpoint,data)
        event('checkpoint',nodeId=node,path=str(checkpoint),label=LABELS[node]+'检查点')
        event('node',id=node,state='succeeded')
    event('artifact',path=str(artifact),label='本次使用的构建产物')
    event('result',data=dict(source=parameters['source'],environment=parameters['environment'],
                            artifactSha256=artifact_sha,note='仅模拟本地部署，没有连接服务器'))


def main():
    stage=sys.argv[1]
    if stage=='options':
        candidates=[]
        for path in sorted((DATA/'sources').iterdir()):
            if path.is_dir():
                candidates.append(dict(label=path.name,value=dict(directory=str(path.resolve()),fingerprint=sha(path/'source.txt')),
                                       details=[dict(label='目录',value=str(path.resolve())),dict(label='修改状态',value='按内容摘要固定版本')]))
        print(json.dumps(dict(options=candidates,default=candidates[0]['value']),ensure_ascii=False))
        return
    parameters=read(os.environ['PROJECT_ACTIONS_PARAMETERS_FILE'])
    context=read(os.environ['PROJECT_ACTIONS_CONTEXT_FILE'])
    if stage=='preview':
        try:data=preview(parameters,context)
        except (OSError,ValueError,KeyError,subprocess.CalledProcessError) as exc:data=dict(allowed=False,reason=str(exc))
        print(json.dumps(data,ensure_ascii=False));return
    if stage!='run':raise ValueError('未知阶段')
    run(parameters,context)


if __name__=='__main__':main()
