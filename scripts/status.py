#!/usr/bin/env python3
"""Read bridge status; optionally journal and deliver debounced incident/recovery webhooks."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from render import ROOT, read_env


def command(args):
    return subprocess.run(args, text=True, capture_output=True, timeout=10, check=True).stdout.strip()


def collect(root, prefix):
    issues = []
    checks = {}
    def issue(level, message): issues.append({'level': level, 'message': message})
    def cli(text):
        return command(['docker', 'compose', '--project-directory', str(root), 'exec', '-T', 'asterisk', 'asterisk', '-rx', text])
    try:
        container = command(['docker', 'compose', '--project-directory', str(root), 'ps', '--all', '-q', 'asterisk'])
        info = json.loads(command(['docker', 'inspect', container]))[0] if container else {}
        checks['container'] = info.get('State', {}).get('Health', {}).get('Status', 'missing')
        if checks['container'] != 'healthy' or not info.get('State', {}).get('Running'):
            issue('critical', 'Asterisk container is missing, stopped or unhealthy.')
    except (OSError, ValueError, subprocess.SubprocessError):
        checks['container'] = 'unknown'; issue('critical', 'Cannot inspect the Asterisk container.')
    for name, request in [('registration', 'pjsip show registrations'), ('contacts', 'pjsip show contacts')]:
        try:
            output = cli(request)
            if name == 'registration':
                good = bool(re.search(r'^\s*hringdu-registration/\S+\s+\S+\s+Registered\b', output, re.MULTILINE))
                checks[name] = 'registered' if good else 'not registered'
                if not good: issue('critical', 'Hringdu registration is not Registered.')
            else:
                available = [i for i in range(1, 4) if re.search(r'^\s*Contact:\s+teams' + str(i) + r'-aor/\S+\s+\S+\s+Avail\b', output, re.MULTILINE)]
                checks['teams_peers_available'] = len(available)
                if not available: issue('critical', 'No Teams peer answers outbound SIP OPTIONS successfully.')
                elif len(available) < 3: issue('warning', f'Only {len(available)} of 3 Teams peers are available.')
        except (OSError, subprocess.SubprocessError):
            issue('critical', f'Cannot read Asterisk {name}.')
    tls = root / '.local/tls/fullchain.pem'
    try:
        fqdn = read_env(root / '.env')['SBC_FQDN']
        command(['openssl', 'x509', '-in', str(tls), '-noout', '-checkhost', fqdn])
        end = command(['openssl', 'x509', '-in', str(tls), '-noout', '-enddate']).removeprefix('notAfter=')
        expires = datetime.strptime(end, '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
        days = (expires - datetime.now(timezone.utc)).total_seconds() / 86400
        checks['certificate_days_remaining'] = round(days, 1)
        if days < 3: issue('critical', 'TLS certificate expires within 3 days or has expired.')
        elif days < 14: issue('warning', 'TLS certificate expires within 14 days.')
    except (KeyError, OSError, ValueError, subprocess.SubprocessError):
        issue('critical', 'TLS certificate cannot be read or does not match SBC_FQDN.')
    try:
        command(['systemctl', 'is-active', prefix + '-renew.timer'])
        result = command(['systemctl', 'show', prefix + '-renew.service', '--property=Result', '--value'])
        checks['renewal'] = result
        if result != 'success': issue('warning', 'The last certificate renewal service run failed.')
    except (OSError, subprocess.SubprocessError):
        issue('critical', 'Certificate renewal timer is not active or cannot be inspected.')
    try:
        command(['nft', 'list', 'table', 'inet', 'hringbru'])
    except (OSError, subprocess.SubprocessError):
        issue('critical', 'Application firewall table is missing or cannot be inspected.')
    free = shutil.disk_usage(root).free
    checks['disk_free_mib'] = free // 1048576
    if free < 250 * 1048576: issue('critical', 'Less than 250 MiB disk space remains.')
    elif free < 1024 * 1048576: issue('warning', 'Less than 1 GiB disk space remains.')
    if (root / '.local/deployment-pending.json').exists(): issue('critical', 'An interrupted deployment requires recovery.')
    level = 'critical' if any(i['level'] == 'critical' for i in issues) else 'warning' if issues else 'ok'
    return {'status': level, 'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': checks, 'issues': issues}


def transition(state, report, now):
    """Three bad samples open an incident; two good samples recover; reminders every 6h."""
    bad = report['status'] != 'ok'
    state['bad'] = state.get('bad', 0) + 1 if bad else 0
    state['good'] = state.get('good', 0) + 1 if not bad else 0
    event = None
    if bad and state['bad'] >= 3:
        if not state.get('incident') or (report['status'] == 'critical' and state.get('alert_level') == 'warning'): event = 'incident'
        elif now - state.get('last_sent', 0) >= 21600: event = 'reminder'
    elif not bad and state.get('incident') and state['good'] >= 2:
        event = 'recovery'
    return event


def deliver(root, event, report):
    # No raw CLI output, caller IDs, SIP credentials or certificate keys are included.
    payload = {'service': 'hringbru', 'event': event, **report}
    print(json.dumps(payload), flush=True)
    secret = root / 'secrets/alert_webhook_url'
    if not secret.exists(): return True  # Journal-only monitoring is the default.
    try:
        url = secret.read_text().strip()
        if not url.startswith('https://'): raise ValueError('HTTPS required')
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        # Do not forward private webhook credentials to a redirect target.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): return None
        with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
            if not 200 <= response.status < 300: return False
        return True
    except Exception:
        print('Webhook delivery failed; URL withheld. Will retry on the next monitoring run.', file=sys.stderr)
        return False


def monitor(root, report):
    path = root / '.local/monitor-state.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    event = transition(state, report, now)
    if event and deliver(root, event, report):
        state['incident'] = event != 'recovery'
        state['last_sent'] = now
        state['alert_level'] = report['status']
    temp = path.with_suffix('.new')
    temp.write_text(json.dumps(state)); temp.chmod(0o600); temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--monitor', action='store_true')
    parser.add_argument('--service-prefix', choices=['hringbru', 'sipsip'], default='hringbru')
    args = parser.parse_args()
    os.umask(0o077)
    # Share the deployment/renewal lock, suppressing planned maintenance samples.
    with open('/run/lock/' + args.service_prefix + '-maintenance.lock', 'a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Maintenance is running; status sampling skipped.')
            return 0
        report = collect(ROOT, args.service_prefix)
        if args.monitor:
            monitor(ROOT, report)
            # Record every sample so journal-only installations also expose freshness.
            print(f"Status: {report['status']} ({report['checked_at']})", flush=True)
            return 0
        if args.json: print(json.dumps(report, indent=2))
        else:
            print(f"Hringbru: {report['status']}")
            for key, value in report['checks'].items(): print(f'  {key}: {value}')
            for item in report['issues']: print(f"  {item['level']}: {item['message']}")
        return {'ok': 0, 'warning': 1, 'critical': 2}[report['status']]


if __name__ == '__main__': sys.exit(main())
