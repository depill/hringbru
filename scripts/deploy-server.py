#!/usr/bin/env python3
"""Apply a staged Ansible release while holding the shared maintenance flock."""
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
from render import ROOT, checked, read_env, render, validate


def run(args, *, capture=False):
    return subprocess.run(args, check=True, text=True, capture_output=capture)


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
        if subprocess.run(args, capture_output=True).returncode:
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


def main():
    os.umask(0o077)
    if os.geteuid() != 0 or ROOT.parent.name != 'releases' or not re.fullmatch('[a-f0-9]{64}', ROOT.name):
        raise ValueError('Run this server helper through ./bin/deploy (Ansible managed releases only).')
    base = ROOT.parent.parent
    shared = base / 'shared'
    if (ROOT / '.local').resolve() != shared.resolve():
        raise ValueError('Release .local must point to the managed shared data directory.')
    values = read_env(ROOT / '.env')
    if base == Path('/opt/sipsip') and values.get('HRINGBRU_PROJECT_NAME') != 'sipsip':
        raise ValueError('Keep HRINGBRU_PROJECT_NAME=sipsip when upgrading /opt/sipsip to preserve the existing Docker data volume.')
    password = (ROOT / 'secrets/hringdu_password').read_text().removesuffix('\n')
    validate(values, password)
    checked(values, 'ACME_EMAIL', r'[^\s@]+@[^\s@]+\.[^\s@]+')
    addresses = {row[4][0] for row in socket.getaddrinfo(values['SBC_FQDN'], None, socket.AF_INET)}
    if addresses != {values['PUBLIC_IPV4']}:
        raise ValueError('SBC_FQDN must resolve directly and only to PUBLIC_IPV4. Check the DNS-only A record.')
    preview = ROOT / '.preview/asterisk'
    render(values, password, preview)
    firewall = preview.parent / 'hringbru.nft'
    run(['nft', '-c', '-f', str(firewall)], capture=True)
    print('Building the image on the destination architecture.', flush=True)
    compose('build', 'asterisk')
    container = compose('ps', '--status', 'running', '-q', 'asterisk', capture=True).stdout.strip()
    healthy = False
    image_matches = False
    if container:
        healthy = run(['docker', 'inspect', '--format', '{{.State.Health.Status}}', container], capture=True).stdout.strip() == 'healthy'
        running_image = run(['docker', 'inspect', '--format', '{{.Image}}', container], capture=True).stdout.strip()
        desired_image = run(['docker', 'image', 'inspect', '--format', '{{.Id}}', 'hringbru-asterisk:22.11.0'], capture=True).stdout.strip()
        image_matches = running_image == desired_image
    marker = shared / 'deployed-version'
    previous = marker.read_text().strip() if marker.exists() else ''
    cert_ok = certificate_ready(shared / 'tls', values['SBC_FQDN'])
    config_present = all((shared / 'asterisk' / name).is_file() for name in ('pjsip.conf', 'extensions.conf'))
    needs_restart = previous != ROOT.name or not healthy or not image_matches or not cert_ok or not config_present
    if needs_restart and container:
        check_idle(compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'core show channels count', capture=True).stdout)
    if needs_restart:
        render(values, password, shared / 'asterisk')
    # Apply before Asterisk starts; touches only the dedicated hringbru table.
    atomic_text(shared / 'hringbru.nft', firewall.read_text())
    run(['nft', '-f', str(shared / 'hringbru.nft')], capture=True)
    if not cert_ok:
        print('Obtaining/installing the SIP TLS certificate.', flush=True)
        compose('--profile', 'certificates', 'pull', 'certbot')
        run(['/usr/bin/python3', str(ROOT / 'scripts/certificates.py'), 'issue'])
    else:
        # Reinstall/repair mounted permissions even when the ACME certificate is valid.
        run(['/usr/bin/python3', str(ROOT / 'scripts/certificates.py'), 'install'])
    args = ['up', '-d', '--no-build', '--wait', '--wait-timeout', '180']
    if needs_restart:
        # Recheck after ACME issuance; a new call could have arrived meanwhile.
        if container:
            current = compose('ps', '--status', 'running', '-q', 'asterisk', capture=True).stdout.strip()
            if current:
                check_idle(compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'core show channels count', capture=True).stdout)
        args.append('--force-recreate')
    compose(*args, 'asterisk')
    link = base / 'current.new'
    link.unlink(missing_ok=True)
    link.symlink_to(ROOT)
    link.replace(base / 'current')
    atomic_text(marker, ROOT.name + '\n')
    print('DEPLOYED: bridge is healthy.' if needs_restart else 'UNCHANGED: bridge is healthy; no restart needed.')
    print('Carrier registration, Teams pairing and real calls still require live acceptance.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Deployment failed: {error}', file=sys.stderr)
        sys.exit(1)
