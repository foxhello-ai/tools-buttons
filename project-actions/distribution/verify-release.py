#!/usr/bin/env python3
"""Verify the generated installer using isolated paths and real packaged processes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
import uuid


def wait_for(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.05)
    assert predicate(), 'Timed out waiting for packaged process'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installer', type=Path)
    parser.add_argument('--native', action='store_true', help='Also check native handlers and embedded WebKit')
    args = parser.parse_args()
    installer = args.installer.resolve()
    environment = dict(os.environ, PATH='/usr/bin:/bin:/usr/sbin:/sbin', LC_ALL='C', PYTHONDONTWRITEBYTECODE='1')
    with tempfile.TemporaryDirectory(prefix='project actions release ') as temporary:
        root = Path(temporary)
        project = root / "项目 spaces ' $(literal)"
        project.mkdir()
        original_agents = '# Existing project guidance\nKeep this content.\n'
        (project / 'AGENTS.md').write_text(original_agents)
        install_root = root / 'Application Support/ProjectActions'
        bin_dir = root / 'local bin'
        flags = ['--project', str(project), '--no-launch', '--install-dir', str(install_root), '--bin-dir', str(bin_dir)]
        # Use a pipe, as the public curl | bash command does; don't rely on a seekable script file.
        with installer.open('rb') as stream:
            producer = subprocess.Popen(['/bin/cat'], stdin=stream, stdout=subprocess.PIPE)
            try:
                subprocess.run(['/bin/bash', '-s', '--'] + flags, stdin=producer.stdout, env=environment, check=True, timeout=90)
            finally:
                producer.stdout.close()
                producer.wait(timeout=5)
        cli = bin_dir / 'project-actions'
        subprocess.run([str(cli), 'version'], env=environment, check=True)
        subprocess.run([str(cli), 'validate', str(project)], env=environment, check=True)
        local = project / '.project-actions'
        for name in ('AI_GUIDE.md', 'PROTOCOL.md', 'PARAMETERS_AND_RESUME.md', 'AGENTS.md', 'schema.json'):
            assert (local / name).is_file(), name
        assert (project / 'AGENTS.md').read_text().startswith(original_agents)
        assert (project / 'AGENTS.md').read_text().count('<!-- project-actions:start -->') == 1
        config_path = local / 'actions.json'
        config = json.loads(config_path.read_text())
        config['actions'][0]['tooltip'] = 'My preserved custom tooltip'
        config_path.write_text(json.dumps(config))
        script = local / 'scripts/directory.sh'
        script.write_text(script.read_text() + '\n# preserved custom script\n')
        before = {path: sha(path) for path in (config_path, script, project / 'AGENTS.md')}
        subprocess.run(['/bin/bash', str(installer)] + flags, env=environment, check=True, timeout=90)
        subprocess.run([str(cli), 'add', str(project), '--no-launch'], env=environment, check=True)
        assert all(sha(path) == checksum for path, checksum in before.items()), 'Repeat install must preserve custom project files'
        guide=local/'AI_GUIDE.md'
        original_guide=guide.read_text()+'\nProject-local documentation.\n'
        guide.write_text(original_guide)
        subprocess.run([str(cli),'docs',str(project)],env=environment,check=True)
        assert list((local/'.docs-backup').glob('*/AI_GUIDE.md'))[0].read_text()==original_guide
        assert all(sha(path) == checksum for path, checksum in before.items())
        subprocess.run([str(cli),'example',str(project)],env=environment,check=True)
        assert (local/'scripts/resumable-demo/README.md').is_file()
        assert json.loads(config_path.read_text())['project']['id']==config['project']['id']
        subprocess.run([str(cli),'validate',str(project)],env=environment,check=True)
        current = (install_root / 'current').resolve()
        resources = current / 'ProjectActions.app/Contents/Resources'
        python = resources / ('Python-%s/bin/python3' % platform.machine())
        native = current / 'ProjectActions.app/Contents/MacOS/DirectoryButton'
        metadata = subprocess.check_output([str(python), '-I', '-B', '-c', 'import sys,json,ssl; print(json.dumps({"executable":sys.executable,"version":sys.version_info[:2]}))'], env=environment)
        metadata = json.loads(metadata)
        assert Path(metadata['executable']).resolve() == python.resolve()
        assert metadata['version'] == [3, 12]
        architectures = subprocess.check_output(['xcrun', 'lipo', '-archs', str(native)], env=environment, text=True)
        assert set(architectures.split()) == {'x86_64', 'arm64'}
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(current / 'ProjectActions.app')], check=True)
        service = subprocess.Popen([str(python), '-I', '-B', str(resources / 'ProjectActions/project_actions.py'),
                                    'serve', '--project', str(project)], env=environment,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            runtime = local / '.runtime.json'
            wait_for(runtime.exists)
            descriptor = json.loads(runtime.read_text())
            def api(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                request = Request(descriptor['url'] + path, data=data, headers={
                    'Authorization': 'Bearer ' + descriptor['token'], 'Content-Type': 'application/json'})
                with urlopen(request, timeout=5) as response:
                    return json.load(response)
            run = api('/api/runs', {'actionId': 'project-check'})
            final = {}
            def done():
                nonlocal final
                final = api('/api/runs/' + run['id'])
                return final['status'] in {'succeeded', 'failed', 'cancelled', 'timed_out'}
            wait_for(done)
            assert final['status'] == 'succeeded', final
            assert [node['state'] for node in final['nodes']] == ['succeeded'] * 3
            assert final['nodes'][1]['progress'] == 100
            assert len(final['artifacts']) == 1
            events = [json.loads(line) for line in (local / 'runs' / run['id'] / 'events.jsonl').read_text().splitlines()]
            assert any(event['type'] == 'log' and event.get('nodeId') == 'inspect' for event in events)
            assert any(event['type'] == 'log' and event.get('source') == 'stdout' for event in events)
            choices=api('/api/actions/resumable-demo/options')
            parameters=dict(source=choices['parameters'][0]['options'][1]['value'],environment='sandbox',delay=2)
            preview=api('/api/actions/resumable-demo/preview',dict(optionsId=choices['optionsId'],parameters=parameters))
            assert preview['allowed']
            parent=api('/api/runs',dict(actionId='resumable-demo',previewId=preview['previewId']))
            wait_for(lambda:any(node['id']=='deploy' and node['state']=='running' for node in api('/api/runs/'+parent['id'])['nodes']))
            api('/api/runs/'+parent['id']+'/cancel',{})
            wait_for(lambda:api('/api/runs/'+parent['id'])['status']=='cancelled')
            original={p:sha(p) for p in (local/'runs'/parent['id']).iterdir() if p.is_file()}
            preview=api('/api/runs/'+parent['id']+'/resume-preview',dict(nodeId='deploy'))
            assert [step['operation'] for step in preview['plan']]==['reuse','reuse','run','run']
            resumed=api('/api/runs',dict(actionId='resumable-demo',previewId=preview['previewId']))
            wait_for(lambda:api('/api/runs/'+resumed['id'])['status'] in {'succeeded','failed'})
            final=api('/api/runs/'+resumed['id'])
            assert final['status']=='succeeded',final
            assert final['parentRunId']==parent['id'] and final['parameters']==parameters
            assert sum(node.get('execution')=='reused' for node in final['nodes'])==2
            assert final['result']['artifactSha256']
            assert all(sha(p)==checksum for p,checksum in original.items())
        finally:
            service.terminate()
            service.wait(timeout=8)
        if args.native:
            fixture = root / 'native fixture'
            subprocess.run([str(python), '-I', '-B', str(resources / 'ProjectActions/project_actions.py'), 'init', str(fixture)], env=environment, check=True)
            try:
                subprocess.run([str(native), '--verify-actions', '--directory', str(fixture),
                                '--preferences-suite', 'local.project-actions.release.' + uuid.uuid4().hex],
                               env=environment, check=True, timeout=40)
                wait_for(lambda: not (fixture / '.project-actions/.runtime.json').exists())
            finally:
                runtime = fixture / '.project-actions/.runtime.json'
                if runtime.exists():
                    try:
                        os.kill(json.loads(runtime.read_text())['pid'], 15)
                    except (ProcessLookupError, FileNotFoundError):
                        pass
            fixture=root/'native resume fixture'
            subprocess.run([str(python),'-I','-B',str(resources/'ProjectActions/project_actions.py'),'init',str(fixture)],env=environment,check=True)
            subprocess.run([str(cli),'example',str(fixture)],env=environment,check=True)
            try:
                subprocess.run([str(native),'--verify-resume','--directory',str(fixture),
                                '--preferences-suite','local.project-actions.resume-release.'+uuid.uuid4().hex],
                               env=environment,check=True,timeout=70)
                wait_for(lambda:not (fixture/'.project-actions/.runtime.json').exists())
            finally:
                try:
                    os.kill(json.loads((fixture/'.project-actions/.runtime.json').read_text())['pid'],15)
                except (ProcessLookupError, FileNotFoundError):pass
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(current / 'ProjectActions.app')], check=True)
        # A damaged payload must fail before changing install/project files.
        damaged = root / 'damaged.run'
        data = installer.read_bytes()
        marker = b'install_payload "$@"; exit $?\n'
        index = data.index(marker) + len(marker)
        data = data[:index] + (b'A' if data[index:index+1] != b'A' else b'B') + data[index+1:]
        damaged.write_bytes(data)
        bad_root = root / 'bad install'
        bad_project = root / 'bad project'
        bad_project.mkdir()
        result = subprocess.run(['/bin/bash', str(damaged), '--project', str(bad_project), '--no-launch',
                                 '--install-dir', str(bad_root), '--bin-dir', str(root / 'bad bin')],
                                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
        assert result.returncode != 0
        assert not bad_root.exists() and not (bad_project / '.project-actions').exists()
    print('PASS: piped install without external Python; preserved project files; guide upgrade backup; universal binary; bundled Shell/logs/progress/graph/report; parameter preview, stop and node resume with intact original history; checksum rejection.')


if __name__ == '__main__':
    main()
