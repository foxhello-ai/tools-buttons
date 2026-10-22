"""End-to-end local script fixtures: inputs → preview → cancel → resume → history."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import project_actions as app
from action_inputs import probe, validate


class ParametersResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix="任务 参数 ' $(literal) ")
        self.project=Path(self.tmp.name).resolve()/'中文 project'
        with contextlib.redirect_stdout(io.StringIO()):app.initialize(self.project)
        subprocess.run([sys.executable,str(app.ROOT/'examples/resumable-pipeline/install.py'),str(self.project)],check=True,stdout=subprocess.DEVNULL)
        self.config_path=self.project/app.CONFIG_PATH
        self.runner=app.Runner(self.project)
        self.server=app.ActionServer(('127.0.0.1',0),self.runner)
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.02},daemon=True);self.thread.start()

    def tearDown(self):
        self.runner.shutdown();self.server.shutdown();self.server.server_close();self.thread.join(2);self.tmp.cleanup()

    def request(self,path,body=None,token=None):
        data=json.dumps(body).encode() if body is not None else None
        request=Request(self.runner.base_url+path,data=data,headers={'Authorization':'Bearer '+(token or self.server.token),'Content-Type':'application/json'})
        with urlopen(request,timeout=12) as response:return json.load(response)

    def configure(self,change):
        data=json.loads(self.config_path.read_text());action=data['actions'][-1];change(action);self.config_path.write_text(json.dumps(data));return action

    def preview(self,delay=.6):
        choices=self.request('/api/actions/resumable-demo/options')
        source=choices['parameters'][0]['options'][1]['value']
        data=self.request('/api/actions/resumable-demo/preview',dict(optionsId=choices['optionsId'],parameters=dict(source=source,environment='sandbox',delay=delay)))
        self.assertTrue(data['allowed'],data)
        return data

    def start(self,preview=None):
        data=self.request('/api/runs',dict(actionId='resumable-demo',previewId=(preview or self.preview())['previewId']))
        return self.runner.runs[data['id']]

    def wait(self,run):
        end=time.monotonic()+12
        while time.monotonic()<end:
            if run.status in app.TERMINAL:return run.snapshot()
            time.sleep(.02)
        self.fail('Task timed out: '+str(run.snapshot()))

    def wait_node(self,run,node):
        end=time.monotonic()+8
        while time.monotonic()<end:
            if run.nodes[node]['state']=='running':return
            self.assertNotIn(run.status,app.TERMINAL,run.reason);time.sleep(.01)
        self.fail('Node did not start')

    def stopped(self,preview=None):
        run=self.start(preview);self.wait_node(run,'deploy');self.runner.cancel(run.id)
        self.assertEqual(self.wait(run)['status'],'cancelled')
        return run

    def resume(self,parent,node='deploy'):
        return self.request('/api/runs/'+parent.id+'/resume-preview',dict(nodeId=node))

    def test_options_preview_cancel_and_full_run(self):
        before=len(self.runner.list_runs());preview=self.preview()
        self.assertEqual(len(self.runner.list_runs()),before)
        self.assertIn('财务 目录',preview['parameters']['source']['directory'])
        run=self.start(preview);result=self.wait(run)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual([x['state'] for x in result['nodes']],['succeeded']*4)
        self.assertEqual(len(result['checkpoints']),4)
        self.assertEqual(result['result']['source'],preview['parameters']['source'])
        self.assertEqual(json.loads((run.directory/'parameters.json').read_text()),preview['parameters'])
        self.assertEqual(result['context']['phase'],'run')
        self.assertEqual(app.Runner(self.project).get_snapshot(run.id)['result'],result['result'])
        with self.assertRaises(HTTPError):self.resume(run)

    def test_cancel_then_resume_reuses_build_and_preserves_original(self):
        parent=self.stopped();original={x.name:x.read_bytes() for x in parent.directory.iterdir() if x.is_file()}
        preview=self.resume(parent);self.assertTrue(preview['allowed'],preview)
        self.assertEqual([x['operation'] for x in preview['plan']],['reuse','reuse','run','run'])
        run=self.start(preview);result=self.wait(run)
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['parentRunId'],parent.id);self.assertEqual(result['rootRunId'],parent.id)
        self.assertEqual(result['parameters'],parent.parameters)
        self.assertEqual([x.get('execution') for x in result['nodes']],['reused','reused',None,None])
        self.assertFalse((run.directory/'built.txt').exists())
        self.assertEqual({x.name:x.read_bytes() for x in parent.directory.iterdir() if x.is_file()},original)
        logs=[x for x in run.events if x['type']=='log']
        self.assertFalse(any(x.get('nodeId')=='build' and x['message'].startswith('开始') for x in logs))

    def test_resume_chain_survives_restart_and_keeps_checkpoint_owner(self):
        first=self.stopped();second=self.stopped(self.resume(first))
        self.assertEqual(second.inherited_checkpoints[1]['runId'],first.id)
        restored=app.Runner(self.project)
        preview=restored.preview_resume(second.id,'deploy')
        self.assertTrue(preview['allowed'],preview)
        self.assertEqual(preview['context']['resume']['rootRunId'],first.id)
        self.assertEqual(preview['context']['resume']['checkpoints'][1]['runId'],first.id)
        # Use the original service's receipt for execution.
        third=self.start(self.resume(second));self.assertEqual(self.wait(third)['status'],'succeeded')

    def test_rebuild_resets_downstream_and_does_not_require_old_build_artifact(self):
        parent=self.stopped();(parent.directory/'built.txt').unlink()
        blocked=self.resume(parent);self.assertFalse(blocked['allowed']);self.assertIn('构建',blocked['reason'])
        preview=self.resume(parent,'build');self.assertTrue(preview['allowed'],preview)
        run=self.start(preview);self.assertEqual(self.wait(run)['status'],'succeeded')
        self.assertTrue((run.directory/'built.txt').exists());self.assertNotEqual(run.nodes['build'].get('execution'),'reused')

    def test_source_changes_and_checkpoint_corruption_rejected(self):
        parent=self.stopped();source=Path(parent.parameters['source']['directory'])/'source.txt';source.write_text('changed')
        self.assertFalse(self.resume(parent)['allowed'])
        with self.assertRaises(HTTPError):self.request('/api/runs',{'actionId':'resumable-demo'})
        source.write_text('财务 目录 的模拟源码\n',encoding='utf-8')
        checkpoint=Path(parent.checkpoints[1]['path']);checkpoint.write_text(checkpoint.read_text()+'\n')
        with self.assertRaises(HTTPError) as error:self.resume(parent)
        self.assertEqual(error.exception.code,409)

    def test_changed_artifact_after_preview_fails_execution_before_publish(self):
        parent=self.stopped();preview=self.resume(parent)
        (parent.directory/'built.txt').write_text('tampered')
        run=self.start(preview);result=self.wait(run)
        self.assertEqual(result['status'],'failed',result)
        self.assertFalse((self.project/'.project-actions/demo-data/environment/current-sandbox.json').exists())

    def test_artifact_changed_during_deploy_stops_before_publish(self):
        run=self.start(self.preview(delay=1.2));self.wait_node(run,'deploy')
        (run.directory/'built.txt').write_text('changed during deploy')
        self.assertEqual(self.wait(run)['status'],'failed')
        self.assertFalse((self.project/'.project-actions/demo-data/environment/current-sandbox.json').exists())

    def test_checkpoint_changed_after_preview_rejected_before_task_creation(self):
        parent=self.stopped();preview=self.resume(parent);file=Path(parent.checkpoints[1]['path']);file.write_text(file.read_text()+'\n')
        before=len(self.runner.list_runs())
        with self.assertRaises(HTTPError):self.start(preview)
        self.assertEqual(len(self.runner.list_runs()),before)

    def test_config_snapshot_receipt_expiry_and_reuse(self):
        preview=self.preview();self.configure(lambda action:action.update(tooltip='changed'))
        with self.assertRaises(HTTPError):self.start(preview)
        preview=self.preview();self.runner.inputs.previews[preview['previewId']]['expires']=0
        with self.assertRaises(HTTPError):self.start(preview)
        preview=self.preview();run=self.start(preview);self.wait(run)
        with self.assertRaises(HTTPError):self.start(preview)

    def test_config_change_during_run_does_not_change_input_or_command(self):
        run=self.start();self.wait_node(run,'deploy');self.configure(lambda a:a.update(command=['false']))
        self.assertEqual(self.wait(run)['status'],'succeeded')
        self.assertNotEqual(run.action['command'],['false'])

    def test_running_parent_and_unknown_nodes_or_config_changes_cannot_resume(self):
        parent=self.start();self.wait_node(parent,'deploy')
        with self.assertRaises(HTTPError):self.resume(parent)
        self.runner.cancel(parent.id);self.wait(parent)
        with self.assertRaises(HTTPError):self.resume(parent,'unknown')
        self.configure(lambda a:a.update(env={'CHANGED':'yes'}))
        with self.assertRaises(HTTPError):self.resume(parent)

    def test_invalid_parameters_and_foreign_tokens(self):
        choices=self.request('/api/actions/resumable-demo/options')
        for parameters in [[],{'unknown':1},{'source':{'directory':'/fake'},'environment':'sandbox','delay':0}]:
            with self.assertRaises(HTTPError):self.request('/api/actions/resumable-demo/preview',dict(optionsId=choices['optionsId'],parameters=parameters))
        with self.assertRaises(HTTPError):self.request('/api/actions/resumable-demo/options',token='foreign')
        with self.assertRaises(HTTPError):self.request('/api/runs',dict(actionId='resumable-demo',command=['false']))
        self.assertFalse(self.runner.list_runs())

    def test_disabled_option_and_receipt_config_binding(self):
        action=self.configure(lambda a:a['parameters'][0].update(optionsCommand=[sys.executable,'-c','import json; print(json.dumps({"options":[{"label":"blocked","value":1,"disabledReason":"dirty"}]}))']))
        choices=self.request('/api/actions/resumable-demo/options')
        with self.assertRaises(HTTPError):self.request('/api/actions/resumable-demo/preview',dict(optionsId=choices['optionsId'],parameters=dict(source=1,environment='sandbox',delay=0)))
        self.configure(lambda a:a.update(tooltip='new'))
        with self.assertRaises(HTTPError):self.request('/api/actions/resumable-demo/preview',dict(optionsId=choices['optionsId'],parameters=dict(source=1,environment='sandbox',delay=0)))

    def test_probe_timeout_and_output_limits(self):
        action=self.runner.action('resumable-demo');action['inputTimeout']=.1
        with self.assertRaises(app.Problem):probe(self.project,action,[sys.executable,'-c','import time; time.sleep(3)'],{},dict(mode='new'))
        with self.assertRaises(app.Problem):probe(self.project,action,[sys.executable,'-c','print("x"*1100000)'],{},dict(mode='new'))
        with self.assertRaises(app.Problem):probe(self.project,action,[sys.executable,'-c','print("bad json")'],{},dict(mode='new'))
        with self.assertRaises(app.Problem):probe(self.project,action,[sys.executable,'-c','print(\'{"options": [], "default": NaN}\')'],{},dict(mode='new'))

    def test_malformed_config_and_provider_plan_return_validation_errors(self):
        action=self.runner.action('resumable-demo')
        action['parameters'][0]['type']=[]
        with self.assertRaises(app.Problem):validate(action)
        for plan in [[dict(nodeId=[],operation='run')],[dict(nodeId='check',operation=[])],
                     [dict(nodeId='check',operation='reuse',checkpointId=[])]]:
            self.configure(lambda a:a.update(previewCommand=[sys.executable,'-c','print('+repr(json.dumps(dict(allowed=True,plan=plan)))+')']))
            with self.assertRaises(HTTPError) as error:self.preview()
            self.assertEqual(error.exception.code,400)

    def test_bash_checkpoint_and_reuse_helpers(self):
        script=self.project/'.project-actions/scripts/shell-test.sh'
        script.write_text('''#!/bin/bash
set -euo pipefail
source "$PROJECT_ACTIONS_SHELL"
checkpoint_id="$($PROJECT_ACTIONS_PYTHON - <<'PY'
import json,os
c=json.load(open(os.environ['PROJECT_ACTIONS_CONTEXT_FILE']))
print(c.get('plan',[{}])[0].get('checkpointId',''))
PY
)"
if [[ -n "$checkpoint_id" ]]; then
  pa_reuse prepare "$checkpoint_id"
else
  pa_node prepare running
  printf '{"version":1}\\n' > "$PROJECT_ACTIONS_RUN_DIR/checkpoint.json"
  pa_checkpoint prepare checkpoint.json --label "Shell 检查点"
  pa_node prepare succeeded
fi
pa_node finish running
sleep 1
pa_node finish succeeded
''',encoding='utf-8')
        code='''import json,os
c=json.load(open(os.environ['PROJECT_ACTIONS_CONTEXT_FILE']))
p=[dict(nodeId='prepare',operation='run'),dict(nodeId='finish',operation='run')]
if c['mode']=='resume':p[0]=dict(nodeId='prepare',operation='reuse',checkpointId=c['resume']['checkpoints'][0]['id'])
print(json.dumps(dict(allowed=True,plan=p)))'''
        config=json.loads(self.config_path.read_text())
        config['actions'].append(dict(id='shell-test',tooltip='Shell',command=['bash',str(script)],
            previewCommand=[sys.executable,'-c',code],resume=dict(nodes=['finish']),
            workflow=dict(nodes=[dict(id='prepare'),dict(id='finish',dependsOn=['prepare'])])))
        self.config_path.write_text(json.dumps(config))
        preview=self.request('/api/actions/shell-test/preview',dict(parameters={}))
        run_id=self.request('/api/runs',dict(actionId='shell-test',previewId=preview['previewId']))['id']
        parent=self.runner.runs[run_id];self.wait_node(parent,'finish');self.runner.cancel(run_id);self.wait(parent)
        preview=self.resume(parent,'finish')
        run_id=self.request('/api/runs',dict(actionId='shell-test',previewId=preview['previewId']))['id']
        result=self.wait(self.runner.runs[run_id])
        self.assertEqual(result['status'],'succeeded',result)
        self.assertEqual(result['nodes'][0]['execution'],'reused')
        self.assertEqual(result['nodes'][0]['reusedFrom']['runId'],parent.id)

    def test_checkpoint_path_scope_and_forged_reuse_rejected(self):
        run=self.start();self.wait_node(run,'deploy')
        for payload in [dict(type='checkpoint',nodeId='build',path=str(self.project/'AGENTS.md')),
                        dict(type='checkpoint',nodeId='unknown',path=str(run.directory/'built.txt')),
                        dict(type='node',id='build',state='succeeded',execution='reused',checkpointId='fake')]:
            with self.assertRaises(app.Problem):self.runner.report(run.id,run.token,payload)
        self.runner.cancel(run.id);self.wait(run)

    def test_empty_legacy_task_cannot_resume_and_ordinary_action_still_works(self):
        run=self.runner.start('directory');self.assertEqual(self.wait(run)['status'],'succeeded')
        with self.assertRaises(app.Problem):self.runner.preview_resume(run.id,'check')

    def test_script_provider_can_list_existing_git_worktrees(self):
        repo=self.project/'仓库';repo.mkdir()
        def git(*args):return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.PIPE).decode().strip()
        git('init','-b','main');git('config','user.email','fixture@test');git('config','user.name','Fixture')
        (repo/'code.txt').write_text('initial');git('add','.');git('commit','-m','initial')
        worktree=self.project/'财务 worktree';git('worktree','add','-b','finance',str(worktree))
        provider=self.project/'.project-actions/scripts/resumable-demo/worktree-options.py'
        self.configure(lambda a:(a.update(env={'EXAMPLE_REPOSITORY':str(repo)}),a['parameters'][0].update(optionsCommand=[sys.executable,str(provider)])))
        choices=self.request('/api/actions/resumable-demo/options');items=choices['parameters'][0]['options']
        self.assertEqual(len(items),2)
        source=next(x['value'] for x in items if x['value']['directory']==str(worktree))
        preview=self.request('/api/actions/resumable-demo/preview',dict(optionsId=choices['optionsId'],parameters=dict(source=source,environment='sandbox',delay=0)))
        self.assertTrue(preview['allowed']);self.assertEqual(self.wait(self.start(preview))['status'],'succeeded')
        self.assertEqual(git('branch','--show-current'),'main')
        subprocess.run(['git','-C',str(worktree),'checkout','--detach'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertTrue(any('detached HEAD' in x['label'] for x in self.request('/api/actions/resumable-demo/options')['parameters'][0]['options']))
        (worktree/'new.txt').write_text('dirty')
        self.assertTrue(any(x.get('disabledReason') for x in self.request('/api/actions/resumable-demo/options')['parameters'][0]['options']))


if __name__=='__main__':unittest.main()
