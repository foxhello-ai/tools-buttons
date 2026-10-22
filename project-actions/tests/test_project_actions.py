import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import project_actions as app


class ProjectActionsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='project actions test ')
        self.project = Path(self.temporary.name) / 'project A'
        with contextlib.redirect_stdout(io.StringIO()):
            app.initialize(self.project)
        self.runner = app.Runner(self.project)
        self.server = app.ActionServer(('127.0.0.1', 0), self.runner)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.runner.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def request(self, path, body=None, token=None, origin=None, method=None):
        headers = {'Authorization': 'Bearer ' + (token if token is not None else self.server.token)}
        if origin:
            headers['Origin'] = origin
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = Request(self.runner.base_url + path, data=data, headers=headers, method=method)
        return urlopen(request, timeout=10)

    def json_request(self, *args, **kwargs):
        with self.request(*args, **kwargs) as response:
            return json.load(response)

    def wait(self, run, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if run.status in app.TERMINAL:
                return run.snapshot()
            time.sleep(0.02)
        self.fail('Task did not finish: %s' % run.snapshot())

    def configure(self, action):
        path = self.project / app.CONFIG_PATH
        config = json.loads(path.read_text())
        config['actions'].append(action)
        path.write_text(json.dumps(config))

    def python_action(self, action_id, code, timeout=10):
        return {'id': action_id, 'tooltip': action_id, 'icon': 'play',
                'command': [sys.executable, '-u', '-c', code], 'timeout': timeout}

    def test_project_configuration_and_isolation(self):
        second = Path(self.temporary.name) / 'project B'
        with contextlib.redirect_stdout(io.StringIO()):
            app.initialize(second)
        self.assertNotEqual(app.load_config(self.project)['project']['id'], app.load_config(second)['project']['id'])
        self.assertEqual(self.json_request('/api/project')['project']['name'], 'project A')
        with self.assertRaises(app.Problem):
            app.initialize(self.project)
        self.configure(self.python_action('added-later', 'print("new action")'))
        run = self.runner.start('added-later')
        self.assertEqual(self.wait(run)['status'], 'succeeded')
        self.assertFalse((second / '.project-actions/runs').exists())

    def test_shell_events_flow_progress_artifact_and_sse(self):
        run = self.runner.start('project-check')
        snapshot = self.wait(run)
        self.assertEqual(snapshot['status'], 'succeeded', snapshot)
        self.assertEqual([node['state'] for node in snapshot['nodes']], ['succeeded'] * 3)
        self.assertEqual(snapshot['nodes'][1]['progress'], 100)
        self.assertEqual(snapshot['nodes'][2]['dependsOn'], ['count'])
        logs = [event for event in run.events if event['type'] == 'log']
        self.assertTrue(any(event.get('nodeId') == 'inspect' for event in logs))
        self.assertTrue(any(event.get('source') == 'stdout' for event in logs))
        artifact = snapshot['artifacts'][0]
        with self.request('/api/runs/%s/artifacts/%s' % (run.id, artifact['id'])) as response:
            self.assertIn('project A', response.read().decode())
        with self.request('/api/runs/%s/events?after=0' % run.id) as response:
            stream = response.read().decode()
        self.assertIn('"status": "succeeded"', stream)
        self.assertIn('"progress": 100', stream)
        self.assertIn('event: update', stream)
        stored = [json.loads(line) for line in (run.directory / 'events.jsonl').read_text().splitlines()]
        self.assertEqual(len(stored), run.sequence)
        restored = app.Runner(self.project)
        self.assertEqual(restored.get_snapshot(run.id)['status'], 'succeeded')
        events, done, _ = restored.events_after(run.id, 0)
        self.assertTrue(done)
        self.assertEqual(events[-1]['status'], 'succeeded')

    def test_plain_output_working_directory_environment_and_failure(self):
        action = self.python_action('failure', 'import os,sys; print(os.getcwd()); print(os.environ["CUSTOM_VALUE"]); print("problem", file=sys.stderr); sys.exit(7)')
        action['env'] = {'CUSTOM_VALUE': 'literal `value` $(not-a-command)'}
        self.configure(action)
        run = self.runner.start('failure')
        snapshot = self.wait(run)
        self.assertEqual(snapshot['status'], 'failed')
        self.assertEqual(snapshot['exitCode'], 7)
        logs = [event for event in run.events if event['type'] == 'log']
        self.assertTrue(any(event['message'] == str(self.project.resolve()) for event in logs))
        self.assertTrue(any(event['message'] == action['env']['CUSTOM_VALUE'] for event in logs))
        self.assertTrue(any(event.get('source') == 'stderr' and event['message'] == 'problem' for event in logs))

    def test_timeout_cancellation_and_duplicate_run(self):
        self.configure(self.python_action('timeout', 'import time; print("started", flush=True); time.sleep(20)', timeout=0.3))
        run = self.runner.start('timeout')
        self.assertEqual(self.wait(run)['status'], 'timed_out')
        child_code = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(20)"])'
        self.configure(self.python_action('inherited-output', child_code, timeout=0.3))
        inherited = self.runner.start('inherited-output')
        self.assertEqual(self.wait(inherited)['status'], 'timed_out')
        self.configure(self.python_action('cancel', 'import time; time.sleep(20)'))
        run = self.runner.start('cancel')
        with self.assertRaises(app.Problem) as duplicate:
            self.runner.start('cancel')
        self.assertEqual(duplicate.exception.status, 409)
        self.json_request('/api/runs/%s/cancel' % run.id, method='POST')
        self.assertEqual(self.wait(run)['status'], 'cancelled')

    def test_dynamic_nodes_and_scoped_event_authentication(self):
        self.configure(self.python_action('long', 'import time; time.sleep(20)'))
        run = self.runner.start('long')
        deadline = time.monotonic() + 2
        while run.status == 'queued' and time.monotonic() < deadline:
            time.sleep(0.01)
        endpoint = '/api/runs/%s/report' % run.id
        self.json_request(endpoint, {'type': 'node', 'id': 'build', 'state': 'running', 'label': '构建', 'dependsOn': ['inspect']}, token=run.token)
        self.json_request(endpoint, {'type': 'progress', 'id': 'build', 'completed': 1, 'total': 2}, token=run.token)
        self.assertEqual(run.nodes['build']['progress'], 50)
        self.assertIn('inspect', run.nodes)
        with self.assertRaises(HTTPError) as cycle:
            self.json_request(endpoint, {'type': 'node', 'id': 'inspect', 'state': 'pending', 'dependsOn': ['build']}, token=run.token)
        self.assertEqual(cycle.exception.code, 400)
        self.assertEqual(run.nodes['inspect']['dependsOn'], [])
        with self.assertRaises(HTTPError) as rejected:
            self.json_request('/api/runs', {'actionId': 'directory'}, token=run.token)
        self.assertEqual(rejected.exception.code, 403)
        with self.assertRaises(HTTPError):
            self.json_request(endpoint, {'type': 'artifact', 'path': str(Path(__file__))}, token=run.token)
        for event in [{'type': []}, {'type': 'node', 'id': 'build', 'state': []},
                      {'type': 'progress', 'id': 'build', 'completed': float('nan'), 'total': 1}]:
            with self.assertRaises(HTTPError) as malformed:
                self.json_request(endpoint, event, token=run.token)
            self.assertEqual(malformed.exception.code, 400)
        self.runner.cancel(run.id)
        self.wait(run)

    def test_interrupted_history_restores_terminal_graph(self):
        run_id = 'a' * 32
        directory = self.runner.run_root / run_id
        directory.mkdir(parents=True)
        item = {'id': run_id, 'actionId': 'directory', 'title': 'interrupted', 'status': 'running',
                'createdAt': app.now(), 'sequence': 0, 'artifacts': [],
                'nodes': [{'id': 'work', 'state': 'running', 'dependsOn': [], 'label': 'work'}]}
        (directory / 'run.json').write_text(json.dumps(item))
        restored = app.Runner(self.project).get_snapshot(run_id)
        self.assertEqual(restored['status'], 'interrupted')
        self.assertEqual(restored['nodes'][0]['state'], 'cancelled')

    @unittest.skipUnless(os.name == 'posix', 'POSIX process group behavior')
    def test_cancellation_terminates_child_processes(self):
        child = ('import os,signal,sys,time; from pathlib import Path; '
                 'root=Path(os.environ["PROJECT_ACTIONS_RUN_DIR"]); '
                 'signal.signal(signal.SIGTERM, lambda *args: (root.joinpath("child-stopped").write_text("stopped"), sys.exit(0))); '
                 'root.joinpath("child-ready").write_text("ready"); time.sleep(20)')
        parent = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",%r]).wait()' % child
        self.configure(self.python_action('child', parent))
        run = self.runner.start('child')
        deadline = time.monotonic() + 4
        while not (run.directory / 'child-ready').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((run.directory / 'child-ready').exists())
        self.runner.cancel(run.id)
        self.assertEqual(self.wait(run)['status'], 'cancelled')
        deadline = time.monotonic() + 2
        while not (run.directory / 'child-stopped').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue((run.directory / 'child-stopped').exists(), 'Child must receive group termination')

    def test_request_boundaries(self):
        with self.assertRaises(HTTPError) as unauthorized:
            self.json_request('/api/project', token='wrong')
        self.assertEqual(unauthorized.exception.code, 403)
        with self.assertRaises(HTTPError) as origin:
            self.json_request('/api/runs', {'actionId': 'directory'}, origin='https://example.com')
        self.assertEqual(origin.exception.code, 403)
        with self.assertRaises(HTTPError) as injected:
            self.json_request('/api/runs', {'actionId': 'directory', 'command': ['anything']})
        self.assertEqual(injected.exception.code, 400)
        with self.assertRaises(HTTPError) as unknown:
            self.json_request('/api/runs', {'actionId': 'not-configured'})
        self.assertEqual(unknown.exception.code, 404)

    def test_cli_service_discovery_singleton_and_shutdown(self):
        project = Path(self.temporary.name) / 'desktop project'
        with contextlib.redirect_stdout(io.StringIO()):
            app.initialize(project)
        runtime = project / '.project-actions/.runtime.json'
        command = [sys.executable, str(app.ROOT / 'project_actions.py'), 'serve', '--project', str(project)]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not runtime.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            if not runtime.exists():
                if process.poll() is None:
                    process.terminate()
                _, errors = process.communicate(timeout=8)
                self.fail('CLI service did not publish discovery information: exit=%s, stderr=%s' % (
                    process.returncode, errors.decode('utf-8', errors='replace')))
            descriptor = json.loads(runtime.read_text())
            self.assertEqual(descriptor['directory'], str(project.resolve()))
            self.assertEqual(descriptor['pid'], process.pid)
            if os.name != 'nt':
                self.assertEqual(stat.S_IMODE(runtime.stat().st_mode), 0o600)
            request = Request(descriptor['url'] + '/api/project', headers={'Authorization': 'Bearer ' + descriptor['token']})
            with urlopen(request, timeout=3) as response:
                self.assertEqual(json.load(response)['project']['name'], 'desktop project')
            duplicate = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            self.assertEqual(duplicate.returncode, 1)
            self.assertIn(b'already has a running service', duplicate.stderr)
            self.assertEqual(json.loads(runtime.read_text())['pid'], process.pid)
            self.assertIsNone(process.poll())
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=8)
        self.assertFalse(runtime.exists())
        # A stale discovery file cannot prevent a fresh, exclusively locked service.
        runtime.write_text('{}')
        with app.ServiceLease(project) as lease:
            lease.publish(self.server)
            self.assertEqual(json.loads(runtime.read_text())['token'], self.server.token)
        self.assertFalse(runtime.exists())

    def test_invalid_graph_configuration_is_rejected(self):
        action = self.python_action('cycle', 'print("never run")')
        action['workflow'] = {'nodes': [{'id': 'a', 'dependsOn': ['b']}, {'id': 'b', 'dependsOn': ['a']}]}
        self.configure(action)
        with self.assertRaisesRegex(app.Problem, 'cycle'):
            self.runner.start('cycle')

    def test_spawn_error_is_visible(self):
        self.configure({'id': 'missing', 'tooltip': 'Missing', 'command': ['project-actions-command-that-does-not-exist']})
        run = self.runner.start('missing')
        snapshot = self.wait(run)
        self.assertEqual(snapshot['status'], 'failed')
        self.assertIn('project-actions-command-that-does-not-exist', snapshot['reason'])


if __name__ == '__main__':
    unittest.main()
