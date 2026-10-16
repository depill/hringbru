#!/usr/bin/env python3
"""Apply an Ansible release under the maintenance lock; recover interrupted updates."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from render import ROOT, checked, read_env, render, validate


def run(args, *, capture=False):
    return subprocess.run(args, check=True, text=True, capture_output=capture, timeout=900)


def compose(*args, capture=False):
    return run(['docker', 'compose', '--project-directory', str(ROOT), *args], capture=capture)


def check_idle(output):
    match = re.search(r'^(\d+) active channels\s*$', output, re.MULTILINE)
    if not match:
        raise ValueError('Cannot determine active channels; refusing to replace the running bridge.')
    if int(match[1]):
        raise ValueError('Active calls detected. Retry the same deploy command after calls finish.')


def certificate_ready(tls, fqdn):
    for args in (['openssl', 'x509', '-in', str(tls / 'fullchain.pem'), '-noout', '-checkhost', fqdn],
                 ['openssl', 'x509', '-in', str(tls / 'fullchain.pem'), '-noout', '-checkend', '86400']):
        if subprocess.run(args, capture_output=True, timeout=10).returncode:
            return False
    try:
        cert = run(['openssl', 'x509', '-in', str(tls / 'fullchain.pem'), '-pubkey', '-noout'], capture=True).stdout
        key = run(['openssl', 'pkey', '-in', str(tls / 'privkey.pem'), '-pubout'], capture=True).stdout
        return cert == key
    except subprocess.CalledProcessError:
        return False


def atomic_text(path, text, mode=0o600):
    temp = path.with_name(path.name + '.new')
    temp.write_text(text)
    temp.chmod(mode)
    temp.replace(path)


def promote(base, release):
    link = base / 'current.new'
    link.unlink(missing_ok=True)
    link.symlink_to(release)
    link.replace(base / 'current')


class Transaction:
    """Private, durable snapshot; never removes named volumes or ACME account state."""
    paths = ('asterisk', 'tls', 'hringbru.nft', 'deployed-version')

    def __init__(self, base):
        self.base = base
        self.shared = base / 'shared'
        self.path = self.shared / 'rollback'
        self.pending = self.shared / 'deployment-pending.json'

    def begin(self, runtime, old_release):
        # All snapshot files are complete before the pending journal is published.
        shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(mode=0o700)
        for name in self.paths:
            source = self.shared / name
            if source.is_dir(): shutil.copytree(source, self.path / name)
            elif source.exists(): shutil.copy2(source, self.path / name)
        if runtime:
            atomic_text(self.path / 'runtime.json', json.dumps(runtime))
            # A dedicated tag keeps the previous image from dangling-image pruning.
            image = runtime['services']['asterisk']['image']
            run(['docker', 'tag', image, 'hringbru-rollback:' + runtime['name']], capture=True)
        metadata = {'previous_release': str(old_release) if old_release else None, 'had_runtime': bool(runtime), 'project': runtime['name'] if runtime else read_env(ROOT / '.env').get('HRINGBRU_PROJECT_NAME', 'hringbru')}
        atomic_text(self.pending, json.dumps(metadata))

    def restore(self):
        metadata = json.loads(self.pending.read_text())
        # Refuse to terminate calls even when resuming recovery after an interrupted deploy.
        def previous_compose(*args, capture=False):
            return compose('--project-name', metadata['project'], *args, capture=capture)
        current = previous_compose('ps', '--status', 'running', '-q', 'asterisk', capture=True).stdout.strip()
        if current:
            check_idle(previous_compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'core show channels count', capture=True).stdout)
        previous_compose('stop', 'asterisk')
        for name in self.paths:
            target = self.shared / name
            if target.is_dir(): shutil.rmtree(target)
            else: target.unlink(missing_ok=True)
            source = self.path / name
            if source.is_dir(): shutil.copytree(source, target)
            elif source.exists(): shutil.copy2(source, target)
        if (self.shared / 'hringbru.nft').exists():
            run(['nft', '-f', str(self.shared / 'hringbru.nft')], capture=True)
        # On first install retain the restrictive candidate firewall, with Asterisk stopped.
        if metadata['had_runtime']:
            run(['docker', 'compose', '-f', str(self.path / 'runtime.json'), 'up', '-d', '--no-build',
                 '--pull', 'never', '--wait', '--wait-timeout', '180', '--force-recreate', 'asterisk'])
        if metadata['previous_release']:
            promote(self.base, metadata['previous_release'])
        else:
            (self.base / 'current').unlink(missing_ok=True)
        self.pending.unlink()
        print('ROLLED BACK: previous image and mounted configuration restored.' if metadata['had_runtime']
              else 'FIRST INSTALL FAILED: candidate stopped; no previous deployment existed.', flush=True)

    def commit(self):
        self.pending.unlink()


def main():
    os.umask(0o077)
    if os.geteuid() != 0 or ROOT.parent.name != 'releases' or not re.fullmatch('[a-f0-9]{64}', ROOT.name):
        raise ValueError('Run this server helper through ./bin/deploy (Ansible managed releases only).')
    base = ROOT.parent.parent
    shared = base / 'shared'
    if (ROOT / '.local').resolve() != shared.resolve():
        raise ValueError('Release .local must point to the managed shared data directory.')
    values = read_env(ROOT / '.env')
    if (base / 'current/.env').exists():
        old_project = read_env(base / 'current/.env').get('HRINGBRU_PROJECT_NAME', 'hringbru')
        if values.get('HRINGBRU_PROJECT_NAME', 'hringbru') != old_project:
            raise ValueError('HRINGBRU_PROJECT_NAME cannot change across releases; it identifies persistent Docker data.')
    transaction = Transaction(base)
    if transaction.pending.exists():
        print('Recovering an interrupted deployment before applying this release.', flush=True)
        transaction.restore()
    values = read_env(ROOT / '.env')
    if base == Path('/opt/sipsip') and values.get('HRINGBRU_PROJECT_NAME') != 'sipsip':
        raise ValueError('Keep HRINGBRU_PROJECT_NAME=sipsip when upgrading /opt/sipsip to preserve its Docker volume.')
    password = (ROOT / 'secrets/hringdu_password').read_text().removesuffix('\n')
    validate(values, password)
    checked(values, 'ACME_EMAIL', r'[^\s@]+@[^\s@]+\.[^\s@]+')
    image = checked(values, 'ASTERISK_IMAGE', r'[a-z0-9][a-z0-9./_:@-]+')
    addresses = {row[4][0] for row in socket.getaddrinfo(values['SBC_FQDN'], None, socket.AF_INET)}
    if addresses != {values['PUBLIC_IPV4']}:
        raise ValueError('SBC_FQDN must resolve directly and only to PUBLIC_IPV4. Check the DNS-only A record.')
    preview = ROOT / '.preview/asterisk'
    render(values, password, preview)
    firewall = preview.parent / 'hringbru.nft'
    run(['nft', '-c', '-f', str(firewall)], capture=True)
    if values.get('DEPLOY_BUILD') == 'true':
        print('Building explicitly requested local image.', flush=True)
        compose('build', 'asterisk')
    else:
        print('Pulling the selected CI image for the destination architecture.', flush=True)
        compose('pull', 'asterisk')
    desired_image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], capture=True).stdout.strip()
    container = compose('ps', '--all', '-q', 'asterisk', capture=True).stdout.strip()
    runtime = None
    healthy = False
    running_image = ''
    old_release = (base / 'current').resolve() if (base / 'current').exists() else None
    if container:
        inspection = json.loads(run(['docker', 'inspect', container], capture=True).stdout)[0]
        healthy = inspection['State'].get('Health', {}).get('Status') == 'healthy' and inspection['State']['Running']
        running_image = inspection['Image']
        # Read the last successful release's compose definition, including pre-registry deployments.
        source = old_release or ROOT
        runtime = json.loads(run(['docker', 'compose', '--project-directory', str(source), 'config', '--format', 'json'], capture=True).stdout)
        runtime['services']['asterisk']['image'] = running_image
        runtime['services']['asterisk'].pop('build', None)
        runtime['services']['asterisk']['pull_policy'] = 'never'
    marker = shared / 'deployed-version'
    previous = marker.read_text().strip() if marker.exists() else ''
    cert_ok = certificate_ready(shared / 'tls', values['SBC_FQDN'])
    config_present = all((shared / 'asterisk' / name).is_file() for name in ('pjsip.conf', 'extensions.conf'))
    needs_restart = previous != ROOT.name or not healthy or running_image != desired_image or not cert_ok or not config_present
    if not needs_restart:
        print('UNCHANGED: bridge is healthy; no restart needed.')
        return
    if container and inspection['State']['Running']:
        check_idle(compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'core show channels count', capture=True).stdout)
    transaction.begin(runtime, old_release)
    try:
        # Certificates are prepared before changing the mounted application configuration.
        if not cert_ok:
            print('Obtaining/installing the SIP TLS certificate.', flush=True)
            compose('--profile', 'certificates', 'pull', 'certbot')
            run(['/usr/bin/python3', str(ROOT / 'scripts/certificates.py'), 'issue'])
        else:
            run(['/usr/bin/python3', str(ROOT / 'scripts/certificates.py'), 'install'])
        if container and inspection['State']['Running']:
            check_idle(compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'core show channels count', capture=True).stdout)
        render(values, password, shared / 'asterisk')
        atomic_text(shared / 'hringbru.nft', firewall.read_text())
        run(['nft', '-f', str(shared / 'hringbru.nft')], capture=True)
        compose('up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '180', '--force-recreate', 'asterisk')
        promote(base, ROOT)
        atomic_text(marker, ROOT.name + '\n')
        transaction.commit()
    except Exception:
        print('Deployment failed; restoring the previous deployment.', file=sys.stderr, flush=True)
        try:
            transaction.restore()
        except Exception:
            # Keep the snapshot and journal for recovery on the next invocation.
            print('ROLLBACK INCOMPLETE: snapshot retained in shared/rollback; retry after inspecting active calls and Docker.', file=sys.stderr)
        raise
    print('DEPLOYED: bridge is healthy. Carrier registration and real calls still require live acceptance.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Deployment failed: {error}', file=sys.stderr)
        sys.exit(1)
