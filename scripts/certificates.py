#!/usr/bin/env python3
"""Issue/renew Let's Encrypt certificates, install them, then drain/restart Asterisk."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from render import ROOT, checked, read_env


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def compose(*args, **kwargs):
    return run(['docker', 'compose', '--project-directory', str(ROOT), *args], **kwargs)


def install(fqdn):
    src = ROOT / '.local/letsencrypt/live' / fqdn
    cert = src / 'fullchain.pem'
    key = src / 'privkey.pem'
    run(['openssl', 'x509', '-in', str(cert), '-noout', '-checkhost', fqdn])
    run(['openssl', 'x509', '-in', str(cert), '-noout', '-checkend', '86400'])
    certpub = run(['openssl', 'x509', '-in', str(cert), '-pubkey', '-noout'], capture_output=True).stdout
    keypub = run(['openssl', 'pkey', '-in', str(key), '-pubout'], capture_output=True).stdout
    if certpub != keypub:
        raise ValueError('Certificate and private key do not match.')
    target = ROOT / '.local/tls'
    target.mkdir(mode=0o755, exist_ok=True)
    # mkdir's mode is filtered by our private umask. This child is mounted directly
    # into the non-root container; its parent remains private on the host.
    target.chmod(0o755)
    changed = False
    for name in ('fullchain.pem', 'privkey.pem'):
        data = (src / name).read_bytes()
        dest = target / name
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).digest() != hashlib.sha256(data).digest():
            tmp = target / (name + '.new')
            tmp.write_bytes(data)
            tmp.chmod(0o644)  # Only beneath private .local; readable by container UID 10001.
            tmp.replace(dest)
            changed = True
        dest.chmod(0o644)  # Also repair unchanged files created with old permissions.
    if changed:
        running = compose('ps', '--status', 'running', '-q', 'asterisk', capture_output=True, text=True).stdout.strip()
        if running:
            compose('exec', '-T', 'asterisk', 'asterisk', '-rx', 'core restart gracefully')
            print('New certificate installed; Asterisk will restart after active calls finish.')
        else:
            print('Certificate installed. Asterisk is not running; start it after rendering config.')
    else:
        print('Installed certificate is unchanged.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('issue', 'renew', 'install'))
    p.add_argument('--dry-run', action='store_true', help='Test renewal without installing a certificate.')
    args = p.parse_args()
    if args.dry_run and args.action != 'renew':
        p.error('--dry-run is only valid for renew')
    os.umask(0o077)
    local = ROOT / '.local'
    local.mkdir(mode=0o700, exist_ok=True)
    local.chmod(0o700)
    v = read_env(ROOT / '.env')
    fqdn = checked(v, 'SBC_FQDN', r'[a-z0-9.-]+\.[a-z]{2,63}')
    for name in ('letsencrypt', 'acme-lib', 'acme-log'):
        (local / name).mkdir(mode=0o700, exist_ok=True)
    if args.action == 'issue':
        email = checked(v, 'ACME_EMAIL', r'[^\s@]+@[^\s@]+\.[^\s@]+')
        compose('--profile', 'certificates', 'run', '--rm', 'certbot', 'certonly', '--standalone',
                '--non-interactive', '--agree-tos', '--email', email, '--cert-name', fqdn,
                '-d', fqdn, '--key-type', 'rsa', '--rsa-key-size', '2048')
    elif args.action == 'renew':
        command = ['--profile', 'certificates', 'run', '--rm', 'certbot', 'renew', '--standalone',
                   '--non-interactive', '--cert-name', fqdn]
        if args.dry_run:
            command.append('--dry-run')
        compose(*command)
    if not args.dry_run:
        install(fqdn)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as e:
        print(f'Certificate operation failed: {e}', file=sys.stderr)
        sys.exit(1)
