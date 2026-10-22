#!/usr/bin/env python3
"""Merge an isolated demo action into a project; preserve existing buttons and scripts."""
import json
from pathlib import Path
import shutil
import sys


def install(project):
    project=Path(project).resolve();local=project/'.project-actions';config_path=local/'actions.json'
    config=json.loads(config_path.read_text(encoding='utf-8'))
    destination=local/'scripts/resumable-demo'
    if destination.exists() or any(x['id']=='resumable-demo' for x in config['actions']):
        raise ValueError('示例已存在，未覆盖现有文件')
    data=local/'demo-data'
    if data.exists():raise ValueError('demo-data 已存在，未覆盖')
    action=dict(id='resumable-demo',icon='upload',tooltip='参数与节点续跑示例（本地模拟）',
                command=['bash','.project-actions/scripts/resumable-demo/pipeline.sh','run'],
                previewCommand=['bash','.project-actions/scripts/resumable-demo/pipeline.sh','preview'],
                parameters=[dict(name='source',label='来源目录',type='select',optionsCommand=['bash','.project-actions/scripts/resumable-demo/pipeline.sh','options']),
                            dict(name='environment',label='模拟环境',type='select',default='sandbox',options=[dict(label='沙箱',value='sandbox'),dict(label='测试',value='test')]),
                            dict(name='delay',label='模拟部署等待秒数（便于停止演练）',type='number',default=8)],
                resume=dict(nodes=['check','build','deploy','verify']),timeout=120,
                workflow=dict(nodes=[dict(id=node,label=label,dependsOn=[] if i==0 else [['check','build','deploy','verify'][i-1]]) for i,(node,label) in enumerate(zip(['check','build','deploy','verify'],['检查','构建与测试','模拟部署','部署后检查']))]))
    destination.mkdir(parents=True)
    for name in ['pipeline.sh','pipeline.py','worktree-options.py','README.md']:shutil.copy2(Path(__file__).parent/name,destination/name)
    for name in ['主目录','财务 目录']:
        path=data/'sources'/name;path.mkdir(parents=True);(path/'source.txt').write_text(name+' 的模拟源码\n',encoding='utf-8')
    config['actions'].append(action)
    temporary=config_path.with_suffix('.tmp');temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temporary.replace(config_path)
    ignore=local/'.gitignore'
    existing=ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    if 'demo-data/' not in existing.splitlines():ignore.write_text(existing+'\ndemo-data/\n',encoding='utf-8')
    print('已增加本地模拟按钮 resumable-demo；现有按钮已保留。')


if __name__=='__main__':install(sys.argv[1])
