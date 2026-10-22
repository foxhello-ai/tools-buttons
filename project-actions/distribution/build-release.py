#!/usr/bin/env python3
"""Build a self-contained, universal macOS installer using pinned Python runtimes."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import subprocess
import tarfile
import tempfile

TOOL = Path(__file__).resolve().parents[1]
DIST = TOOL / 'distribution'
NATIVE = TOOL.parent / 'directory-button-demo'
PYTHON_RELEASE = '20261020'
PYTHON_VERSION = '3.12.15'
PYTHON_SOURCES = {
    'x86_64': {'target': 'x86_64', 'sha256': 'dfe418e59942d3a73789846a161ba973ea4a1b6e2fa8636bc16ad49c398ff6e9'},
    'arm64': {'target': 'aarch64', 'sha256': 'e54a19fd9af218ddd4ed66bbec65f4e9062724ea848a0ddbd269e37765d9959e'},
}


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def python_archive(arch, cache):
    spec = PYTHON_SOURCES[arch]
    filename = 'cpython-%s+%s-%s-apple-darwin-install_only_stripped.tar.gz' % (
        PYTHON_VERSION, PYTHON_RELEASE, spec['target'])
    url = 'https://github.com/astral-sh/python-build-standalone/releases/download/%s/%s' % (
        PYTHON_RELEASE, filename.replace('+', '%2B'))
    spec['url'] = url
    path = cache / ('python-%s.tar.gz' % arch)
    if not path.exists() or digest(path) != spec['sha256']:
        temporary = path.with_suffix('.download')
        subprocess.run(['curl', '--fail', '--location', '--retry', '2', '--output', str(temporary), url], check=True)
        if digest(temporary) != spec['sha256']:
            temporary.unlink()
            raise RuntimeError('Python archive checksum mismatch: %s' % arch)
        temporary.replace(path)
    return path


def unpack_python(archive, destination):
    with tempfile.TemporaryDirectory(prefix='python-unpack-', dir=str(destination.parent)) as temporary:
        with tarfile.open(archive, 'r:gz') as package:
            for member in package.getmembers():
                path = PurePosixPath(member.name)
                if not path.parts or path.parts[0] != 'python' or path.is_absolute() or '..' in path.parts:
                    raise RuntimeError('Unexpected Python archive member')
                if member.issym() or member.islnk():
                    resolved = (Path(temporary) / member.name).parent / member.linkname
                    if not resolved.resolve().is_relative_to(Path(temporary).resolve()):
                        raise RuntimeError('Unexpected Python archive link')
            package.extractall(temporary)
        shutil.move(str(Path(temporary) / 'python'), str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='0.2.0')
    parser.add_argument('--output-dir', type=Path, default=TOOL.parent / 'releases')
    parser.add_argument('--cache-dir', type=Path, default=TOOL.parent.parent / '.release-cache/project-actions')
    args = parser.parse_args()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?', args.version):
        parser.error('Version must be a semantic version')
    output = args.output_dir.resolve()
    cache = args.cache_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    archives = {arch: python_archive(arch, cache) for arch in PYTHON_SOURCES}
    with tempfile.TemporaryDirectory(prefix='project-actions-build-') as temporary:
        build = Path(temporary)
        payload = build / 'payload'
        payload.mkdir()
        app = payload / 'ProjectActions.app'
        environment = dict(os.environ, PROJECT_ACTIONS_ARCH='universal', PROJECT_ACTIONS_APP_OUTPUT=str(app))
        subprocess.run(['bash', str(NATIVE / 'build-native.sh')], env=environment, check=True)
        resources = app / 'Contents/Resources'
        for arch, archive in archives.items():
            print('Bundling Python: %s' % arch, flush=True)
            unpack_python(archive, resources / ('Python-' + arch))
        info = app / 'Contents/Info.plist'
        metadata = plistlib.loads(info.read_bytes())
        for key in ('ProjectDirectory', 'ProjectID', 'PythonExecutable'):
            metadata.pop(key, None)
        metadata.update(CFBundleIdentifier='local.project-actions.desktop', CFBundleName='Project Actions',
                        CFBundleShortVersionString=args.version, CFBundleVersion=args.version)
        info.write_bytes(plistlib.dumps(metadata))
        for name in ('manage.py', 'install.py', 'project-actions'):
            shutil.copy2(DIST / name, payload / name)
        (payload / 'project-actions').chmod(0o755)
        shutil.copy2(DIST / 'README.md', payload / 'README.md')
        # Preserve upstream licenses; code-sign the final bundle after all resources are added.
        subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)], check=True)
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        build_hash = hashlib.sha256()
        for path in sorted(payload.rglob('*')):
            if path.is_file() and not path.is_symlink():
                build_hash.update(str(path.relative_to(payload)).encode())
                build_hash.update(bytes.fromhex(digest(path)))
        manifest = {'version': args.version, 'buildId': build_hash.hexdigest()[:16],
                    'platform': 'macos', 'architectures': ['x86_64', 'arm64'], 'minMacOS': '13.0',
                    'pythonVersion': PYTHON_VERSION, 'pythonSources': PYTHON_SOURCES,
                    'sourceRepository': 'https://github.com/foxhello-ai/tools-buttons'}
        (payload / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        archive = build / 'payload.tar.gz'
        with tarfile.open(archive, 'w:gz', compresslevel=6) as package:
            package.add(payload, arcname='payload')
        checksum = digest(archive)
        installer = output / 'project-actions-macos.run'
        header = (DIST / 'installer-header.sh').read_text()
        if not header.endswith('\n'):
            header += '\n'
        header = header.replace('@@CHECKSUM@@', checksum).replace('@@PAYLOAD_LINE@@', str(header.count('\n') + 1))
        with installer.open('wb') as stream:
            stream.write(header.encode('utf-8'))
            with archive.open('rb') as data:
                base64.encode(data, stream)
        installer.chmod(0o755)
        (output / 'project-actions-macos.run.sha256').write_text('%s  %s\n' % (digest(installer), installer.name))
        manifest['installerSha256'] = digest(installer)
        manifest['installerBytes'] = installer.stat().st_size
        (output / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print('Built: %s (%.1f MiB)' % (installer, installer.stat().st_size / (1024 * 1024)), flush=True)


if __name__ == '__main__':
    main()
