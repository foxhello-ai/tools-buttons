#!/usr/bin/env python3
"""Optional project-owned provider. Configure EXAMPLE_REPOSITORY; never check out branches."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

repository=Path(os.environ['EXAMPLE_REPOSITORY']).expanduser().resolve()
def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args])
options=[]
for record in git(repository,'worktree','list','--porcelain','-z').split(b'\0\0'):
    fields={}
    for field in record.split(b'\0'):
        if field:
            key,_,value=field.partition(b' ');fields[os.fsdecode(key)]=os.fsdecode(value)
    if 'worktree' not in fields or 'bare' in fields:continue
    path=Path(fields['worktree']).resolve();branch=fields.get('branch','detached HEAD').removeprefix('refs/heads/')
    entry=dict(label=path.name+' · '+branch,value=dict(directory=str(path),branch=branch,commit=fields.get('HEAD'),paths=['.']))
    try:
        dirty=git(path,'status','--porcelain','--untracked-files=all')
        digest=hashlib.sha256()
        for name in sorted(x for x in git(path,'ls-files','-z').split(b'\0') if x):
            file=path/os.fsdecode(name)
            if file.is_symlink() or not file.is_file():raise ValueError('示例不支持符号链接或子模块')
            digest.update(name+b'\0'+file.read_bytes()+b'\0')
        entry['value']['fingerprint']=digest.hexdigest()
        if dirty:entry['disabledReason']='存在未提交/未跟踪修改，请先处理'
        entry['details']=[dict(label='目录',value=str(path)),dict(label='分支',value=branch),dict(label='完整提交',value=fields['HEAD']),dict(label='修改状态',value='有修改' if dirty else '干净')]
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:entry['disabledReason']=str(exc)
    options.append(entry)
print(json.dumps(dict(options=options),ensure_ascii=False))
