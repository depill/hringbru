#!/usr/bin/env python3
"""Exercise snapshot recovery with real Docker containers and deliberately failed health.

Uses the built image as a shell fixture; SIP/call-count behavior is covered separately.
No network, carrier credentials or privileged host operations are used.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('deploy_server', ROOT / 'scripts/deploy-server.py')
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)


def main():
    image = os.environ.get('ASTERISK_IMAGE', 'hringbru-asterisk:22.11.0')
    project = 'hringbru-rollback-test-' + uuid.uuid4().hex[:8]
    candidate = project + ':broken'
    with tempfile.TemporaryDirectory(prefix='hringbru-rollback-') as temp:
        base = Path(temp); base.chmod(0o755)
        shared = base / 'shared'; shared.mkdir(mode=0o755)
        config = shared / 'asterisk'; config.mkdir(); (config / 'version').write_text('previous')
        old = base / 'releases/old'; old.mkdir(parents=True)
        new = base / 'releases/new'; new.mkdir()
        (base / 'current').symlink_to(old)
        (shared / 'deployed-version').write_text('previous')
        (new / '.env').write_text('HRINGBRU_PROJECT_NAME=' + project + '\n')
        definition = {'name': project, 'services': {'asterisk': {
            'image': image, 'entrypoint': ['/bin/sh', '-c'], 'command': ['exec sleep 600'],
            'network_mode': 'none', 'volumes': [f'{config}:/fixture:ro'],
            'healthcheck': {'test': ['CMD-SHELL', 'test "$(cat /fixture/version)" = previous'], 'interval': '1s', 'timeout': '1s', 'retries': 1},
        }}}
        def write(release, value): (release / 'compose.yaml').write_text(json.dumps(value))
        write(old, definition); write(new, definition)
        def compose(release, *args):
            return d.run(['docker', 'compose', '--project-directory', str(release), *args], capture=True)
        real_run = d.run
        def run(args, *, capture=False):
            if 'exec' in args and args[-1] == 'core show channels count':
                return subprocess.CompletedProcess(args, 0, '0 active channels\n', '')
            return real_run(args, capture=capture)
        try:
            compose(old, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '15')
            runtime = json.loads(compose(old, 'config', '--format', 'json').stdout)
            old_id = real_run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'], capture=True).stdout.strip()
            runtime['services']['asterisk']['image'] = old_id
            # Commit a stopped fixture as a distinct candidate image without rebuilding Asterisk.
            container = compose(old, 'ps', '-q', 'asterisk').stdout.strip()
            real_run(['docker', 'commit', '--change', 'LABEL hringbru.fixture=broken', container, candidate], capture=True)
            with patch.object(d, 'ROOT', new), patch.object(d, 'run', side_effect=run):
                tx = d.Transaction(base); tx.begin(runtime, old)
                (config / 'version').write_text('broken')
                definition['services']['asterisk']['image'] = candidate; write(new, definition)
                try:
                    compose(new, 'up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '10', '--force-recreate')
                    raise AssertionError('Broken candidate unexpectedly became healthy')
                except subprocess.CalledProcessError:
                    pass
                # Simulate a fresh deploy process recovering the pending journal.
                d.Transaction(base).restore()
            restored = compose(old, 'ps', '-q', 'asterisk').stdout.strip()
            info = json.loads(real_run(['docker', 'inspect', restored], capture=True).stdout)[0]
            assert info['Image'] == old_id
            assert info['State']['Health']['Status'] == 'healthy'
            assert (config / 'version').read_text() == 'previous'
            assert (shared / 'deployed-version').read_text() == 'previous'
            assert (base / 'current').resolve() == old.resolve()
            assert not (shared / 'deployment-pending.json').exists()
            print('PASS: failed candidate replaced by exact previous image, restored mounted config, healthy container and previous success marker.')
        finally:
            compose(new, 'down', '--remove-orphans')
            subprocess.run(['docker', 'image', 'rm', candidate, 'hringbru-rollback:' + project], capture_output=True)


if __name__ == '__main__': main()
