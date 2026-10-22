#!/usr/bin/env python3
"""Exercise the real native confirmation and node resume path against an isolated example."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parent
CLI=ROOT.parent/'project-actions/project_actions.py'
BINARY=ROOT/'DirectoryButton.app/Contents/MacOS/DirectoryButton'
with tempfile.TemporaryDirectory(prefix='native parameter resume ') as temporary:
    project=Path(temporary)/'项目'
    subprocess.run([sys.executable,str(CLI),'init',str(project)],check=True)
    subprocess.run([sys.executable,str(CLI),'example','--project',str(project)],check=True)
    try:
        subprocess.run([str(BINARY),'--verify-resume','--directory',str(project),'--preferences-suite','local.project-actions.resume.'+uuid.uuid4().hex],check=True,timeout=70,env=dict(os.environ,LC_ALL='C'))
    finally:
        runtime=project/'.project-actions/.runtime.json'
        if runtime.exists():
            import json
            try:os.kill(json.loads(runtime.read_text())['pid'],15)
            except (ProcessLookupError,FileNotFoundError):pass
