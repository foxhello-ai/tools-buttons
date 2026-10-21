#!/usr/bin/env python3
"""Exercise native handlers/WebKit against real isolated services, without desktop input."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
CLI = ROOT.parent / 'project-actions/project_actions.py'
BINARY = ROOT / 'DirectoryButton.app/Contents/MacOS/DirectoryButton'


def wait_for(predicate, message):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.05)
    assert predicate(), message


for reuse in (False, True):
    with tempfile.TemporaryDirectory(prefix='native actions integration ') as temporary:
        project = Path(temporary) / 'project'
        subprocess.run([sys.executable, str(CLI), 'init', str(project)], check=True)
        runtime = project / '.project-actions/.runtime.json'
        external = None
        try:
            if reuse:
                external = subprocess.Popen([sys.executable, str(CLI), 'serve', '--project', str(project)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                wait_for(runtime.exists, 'External service must publish discovery information')
            subprocess.run([str(BINARY), '--verify-actions', '--directory', str(project),
                            '--preferences-suite', 'local.demo.directory-button.verify-actions.' + uuid.uuid4().hex],
                           check=True, timeout=40)
            if reuse:
                assert external.poll() is None, 'Native quit must preserve an independently started service'
                assert json.loads(runtime.read_text())['pid'] == external.pid
                print('PASS: native app reused the existing service and left it running on quit.', flush=True)
            else:
                wait_for(lambda: not runtime.exists(), 'Owned service must stop and remove discovery on native quit')
                print('PASS: native app started and stopped its own service.', flush=True)
        finally:
            if external:
                external.terminate()
                external.wait(timeout=8)
            elif runtime.exists():
                # Reap an owned fixture service even if an assertion crashed the native process.
                pid = json.loads(runtime.read_text())['pid']
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass
                wait_for(lambda: not runtime.exists(), 'Fixture service cleanup failed')
