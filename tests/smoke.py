#!/usr/bin/env python3
"""Run the actual Asterisk image offline with dummy credentials; never place real calls."""
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render', ROOT / 'scripts/render.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
sys.path.insert(0, str(ROOT / 'scripts'))
import certificates


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def main():
    image = os.environ.get('ASTERISK_IMAGE', 'hringbru-asterisk:22.11.0')
    name = 'hringbru-smoke-' + uuid.uuid4().hex[:8]
    run('docker', 'image', 'inspect', image)
    with tempfile.TemporaryDirectory(prefix='hringbru-smoke-') as temp:
        base = Path(temp)
        base.chmod(0o755)
        cfg = base / 'config'
        local = base / '.local'; local.mkdir(mode=0o700)
        tls = local / 'tls'
        certificate_source = local / 'letsencrypt/live/sbc.example.net'
        certificate_source.mkdir(parents=True)
        v = r.read_env(ROOT / '.env.example')
        v.update(DID_NUMBERS=','.join('+354'+str(n) for n in range(5550100,5550120)), SBC_FQDN='sbc.example.net', PUBLIC_IPV4='8.8.8.8', HRINGDU_PORT='65060', LOCAL_NETS='10.0.0.0/24',
                 HRINGDU_HOST='sip.provider.example', HRINGDU_USERNAME='00000000*0001',
                 HRINGDU_CONTACT_USER='00000000*0001', HRINGDU_FROM_USER='00000000*0001',
                 HRINGDU_SIGNAL_CIDRS='9.9.9.9/32', HRINGDU_MEDIA_CIDRS='9.9.9.0/24')
        previous_umask = os.umask(0o077)
        try:
            r.render(v, r'>offline\only;password', cfg)
        finally:
            os.umask(previous_umask)
        # Keep all endpoint types, but remove external DNS dependencies in this no-network fixture.
        pjsip = (cfg / 'pjsip.conf').read_text()
        for host in ('sip.provider.example', 'sip.pstnhub.microsoft.com', 'sip2.pstnhub.microsoft.com', 'sip3.pstnhub.microsoft.com'):
            pjsip = pjsip.replace(host, '127.0.0.1')
        pjsip = pjsip.replace('qualify_frequency=60', 'qualify_frequency=0')
        pjsip = pjsip.replace('127.0.0.1:5061', '127.0.0.1:65061')
        pjsip = pjsip.replace('ca_list_file=/etc/ssl/certs/ca-certificates.crt', 'ca_list_file=/tls/ca.pem')
        pjsip = pjsip.replace('[teams-base](!)', '[teams-base](!)\nmedia_address=127.0.0.1\nbind_rtp_to_media_address=yes')
        (cfg / 'pjsip.conf').write_text(pjsip)
        with (cfg / 'modules.conf').open('a') as f:
            f.write('\nload=func_db.so\nload=res_clioriginate.so\nload=res_pjsip_logger.so\n')
        normalization = {
            '5550100': '+3545550100', '5550119': '+3545550119', '3545550107': '+3545550107',
            '003545550108': '+3545550108', '+3545550109': '+3545550109',
            '00442079460000': '+442079460000', '+442079460000': '+442079460000',
            '123': '', '12345678': '', '0001234567': '',
        }
        cases = []
        with (cfg / 'extensions.conf').open('a') as f:
            f.write('\n[hringbru-test]\nexten => valid,1,Set(CALLERID(num)=5551234)\n same => n,Goto(from-hringdu,+3545550100,1)\n'
                    'exten => validout,1,Set(CALLERID(num)=+3545550119)\n same => n,Goto(from-teams,+3545551234,1)\n')
            for i, (number, expected) in enumerate(normalization.items()):
                ext = f'norm{i}'
                f.write(f'exten => {ext},1,Gosub(normalize-number,s,1({number}))\n'
                        f' same => n,Set(DB(smoke/{ext})=result:${{NORMALIZED}})\n same => n,Hangup()\n')
                cases.append((ext, 'result:' + expected))
            for i, (context, dest, cli) in enumerate((
                ('from-hringdu', '+3545550120', '+3545551234'),
                ('from-hringdu', '00000000*0001', '+3545551234'),
                ('from-teams', '+3545551234', '+3545550120'),
                ('from-teams', '+442079460000', '+3545550100'),
                ('from-teams', '113', '+3545550100'),
                ('from-teams', '+3545551234abc', '+3545550100'),
            )):
                ext = f'reject{i}'
                f.write(f'exten => {ext},1,Set(CALLERID(num)={cli})\n'
                        f' same => n,Set(CHANNEL(hangup_handler_push)=smoke-hangup,s,1({ext}))\n'
                        f' same => n,Goto({context},{dest},1)\n')
                cases.append((ext, 'cause:21'))
            f.write('\n[smoke-hangup]\nexten => s,1,Set(DB(smoke/${ARG1})=cause:${HANGUPCAUSE})\n same => n,Return()\n')
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7',
            '-subj', '/CN=sbc.example.net', '-addext', 'subjectAltName=DNS:sbc.example.net,IP:127.0.0.1',
            '-keyout', str(certificate_source / 'privkey.pem'), '-out', str(certificate_source / 'fullchain.pem'))
        previous_umask = os.umask(0o077)
        try:
            with patch.object(certificates, 'ROOT', base), patch.object(certificates, 'compose',
                    return_value=subprocess.CompletedProcess([], 0, '')):
                certificates.install('sbc.example.net')
        finally:
            os.umask(previous_umask)
        (tls / 'ca.pem').write_bytes((tls / 'fullchain.pem').read_bytes())
        try:
            args = ['docker', 'run', '-d', '--name', name, '--hostname', 'localhost', '--network', 'none', '--read-only',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                    '-v', f'{cfg}:/etc/asterisk:ro', '-v', f'{tls}:/tls:ro']
            for path in ('/var/run/asterisk', '/var/lib/asterisk', '/var/spool/asterisk', '/var/log/asterisk', '/tmp'):
                args += ['--tmpfs', f'{path}:uid=10001,gid=10001,mode=0750']
            run(*args, image)
            def cli(command):
                return run('docker', 'exec', name, 'asterisk', '-rx', command)
            for _ in range(30):
                try:
                    if 'System uptime' in cli('core show uptime'): break
                except subprocess.CalledProcessError: pass
                time.sleep(0.2)
            else: raise AssertionError('Asterisk failed to start')
            # Configuration may still be loading immediately after the control socket opens.
            for _ in range(30):
                if 'Objects found: 4' in cli('pjsip show endpoints'): break
                time.sleep(0.2)
            endpoints = cli('pjsip show endpoints')
            assert all(peer in endpoints for peer in ('hringdu', 'teams1', 'teams2', 'teams3')), endpoints
            assert 'teams-tls' in cli('pjsip show transports')
            aor = cli('pjsip show aor teams1-aor')
            assert re.search(r'qualify_2xx_only\s*:\s*true\b', aor), aor
            for module in ('res_srtp.so', 'chan_pjsip.so'):
                modules = cli('module show like ' + module)
                assert 'Running' in modules and 'Not Running' not in modules, modules
            assert 'sdes' in cli('pjsip show endpoint teams1')
            assert r'>offline\only;password' in cli('pjsip show auth hringdu-auth'), 'Password parser changed escaped semicolon.'
            # Debian's CA package must contain every root in current Microsoft Teams guidance.
            bundle = run('docker', 'exec', name, 'cat', '/etc/ssl/certs/ca-certificates.crt')
            fingerprints = set()
            for i, part in enumerate(bundle.split('-----END CERTIFICATE-----')):
                if '-----BEGIN CERTIFICATE-----' not in part: continue
                der = subprocess.run(['openssl', 'x509', '-outform', 'DER'], input=(part + '-----END CERTIFICATE-----\n').encode('ascii'),
                                     capture_output=True, check=True).stdout
                fingerprints.add(hashlib.sha1(der).hexdigest().upper())
            roots = {'A8985D3A65E5E5C4B2D7D66D40C6DD2FB19C5436', 'DF3C24F9BFD666761B268073FE06D1CC8D4F82A4',
                     '7E04DE896A3E666D00E687D33FFAD93BE83D349E', '17F3DE5E9F0F19E98EF61F32266E20C407AE30EE',
                     'A78849DC5D7C758C8CDE399856B3AAD0B2A57135', '999A64C37FF47D9FAB95F14769891460EEC4C3C5',
                     '73A5E64A3BFF8316FF0EDCCC618A906E4EAE4D74'}
            assert roots <= fingerprints, f'Missing Teams CA roots: {roots - fingerprints}'
            for ext, expected in cases:
                cli(f'channel originate Local/{ext}@hringbru-test application Wait 1')
                result = ''
                for _ in range(20):
                    result = cli(f'database get smoke {ext}')
                    if 'Value:' in result: break
                    time.sleep(0.05)
                assert f'Value: {expected}\n' in result, (ext, expected, result)
            # Capture an actual TLS INVITE to a local test peer, with peer verification enabled.
            run('docker', 'exec', '-d', name, 'sh', '-c',
                'tail -f /dev/null | stdbuf -o0 openssl s_server -accept 65061 -cert /tls/fullchain.pem -key /tls/privkey.pem '
                '-quiet -ign_eof > /tmp/teams-wire 2>/tmp/teams-tls-errors')
            time.sleep(0.2)
            cli('pjsip set logger on')
            cli('channel originate Local/valid@hringbru-test application Wait 1')
            wire = ''
            for _ in range(30):
                wire = run('docker', 'exec', name, 'cat', '/tmp/teams-wire')
                if 'a=crypto:' in wire: break
                time.sleep(0.1)
            assert 'INVITE sip:+3545550100@127.0.0.1:65061' in wire, wire
            assert 'Contact: <sip:hringbru@sbc.example.net:5061;transport=TLS>' in wire, wire
            assert 'SIP/2.0/TLS sbc.example.net:5061' in wire, wire
            assert 'RTP/SAVP' in wire and 'AES_CM_128_HMAC_SHA1_80' in wire, wire
            assert '+3545551234' in wire, wire
            cli('channel request hangup all')
            cli('channel originate Local/validout@hringbru-test application Wait 1')
            logs = ''
            for _ in range(30):
                logs = run('docker', 'logs', name)
                if 'INVITE sip:5551234@127.0.0.1:65060' in logs: break
                time.sleep(0.1)
            assert 'INVITE sip:5551234@127.0.0.1:65060' in logs, 'Outbound national destination was not sent.'
            assert 'P-Asserted-Identity: <sip:5550119@127.0.0.1>' in logs, 'Outbound caller DID was not sent.'
            cli('channel request hangup all')
            logs = logs.replace("Could not find option 'minimum_size' with type 'threadpool' in module 'stasis'", '')  # Upstream 22.11 documentation warning.
            for marker in ('Unable to load module', 'Could not create an object', 'Could not find option',
                           'No application', 'not registered', 'Error parsing', 'syntax error', 'declined to load', 'Failed to resolve dependencies'):
                assert marker not in logs, logs
            arch = run('docker', 'image', 'inspect', image, '--format', '{{.Architecture}}').strip()
            print(f'PASS: Asterisk 22.11.0 ({arch}), 4 endpoints, TLS/SRTP, all 7 Teams CA roots, '
                  f'{len(normalization)} normalizations, {len(cases) - len(normalization)} rejected call routes, FQDN Contact/Via, SRTP INVITE and carrier-format outbound INVITE; network disabled.')
        except Exception:
            print(run('docker', 'exec', name, 'sh', '-c', 'cat /tmp/teams-tls-errors 2>/dev/null || true'))
            print(run('docker', 'logs', name))
            raise
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)


if __name__ == '__main__': main()
