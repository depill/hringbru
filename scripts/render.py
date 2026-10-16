#!/usr/bin/env python3
"""Validate literal deployment settings and render Asterisk config; no SIP/network writes."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parent.parent
TEAMS_NETS = ('52.112.0.0/14', '52.120.0.0/14')


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError('Use literal KEY=value lines in .env (no shell syntax).')
        if key in values:
            raise ValueError(f'Duplicate setting: {key}')
        values[key] = value.strip()
    return values


def checked(values, key, pattern):
    value = values.get(key, '')
    if not re.fullmatch(pattern, value):
        raise ValueError(f'{key} is missing or invalid.')
    return value


def validate(values, password):
    v = dict(values)
    v.setdefault('HRINGBRU_PROJECT_NAME', 'hringbru')
    checked(v, 'HRINGBRU_PROJECT_NAME', r'[a-z0-9][a-z0-9_-]{0,62}')
    host_pattern = r'(?=.{3,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}'
    for key in ('SBC_FQDN', 'HRINGDU_HOST'):
        checked(v, key, host_pattern)
    if v['SBC_FQDN'].endswith('.onmicrosoft.com'):
        raise ValueError('SBC_FQDN must use your verified custom domain.')
    ip = ipaddress.IPv4Address(v.get('PUBLIC_IPV4', ''))
    if not ip.is_global:
        raise ValueError('PUBLIC_IPV4 must be a public IPv4 address.')
    for key in ('HRINGDU_USERNAME', 'HRINGDU_CONTACT_USER'):
        checked(v, key, r'[A-Za-z0-9*+_.-]{1,80}')
    checked(v, 'HRINGDU_FROM_USER', r'[A-Za-z0-9*+_.-]{0,80}')
    checked(v, 'HRINGDU_TRANSPORT', r'udp|tcp')
    checked(v, 'HRINGDU_DID_SOURCE', r'request_uri|to')
    checked(v, 'HRINGDU_DIAL_FORMAT', r'national|e164|international')
    checked(v, 'HRINGDU_CLI_FORMAT', r'national|e164')
    checked(v, 'ALLOW_INTERNATIONAL', r'true|false')
    for key in ('HRINGDU_PORT', 'RTP_START', 'RTP_END'):
        checked(v, key, r'[0-9]{1,5}')
        if not 1024 <= int(v[key]) <= 65535:
            raise ValueError(f'{key} must be 1024-65535.')
    if int(v['RTP_START']) <= 5061 <= int(v['RTP_END']) or int(v['RTP_START']) <= 5060 <= int(v['RTP_END']):
        raise ValueError('RTP ports must not overlap SIP listeners.')
    if int(v['RTP_START']) % 2 or int(v['RTP_END']) - int(v['RTP_START']) < 159:
        raise ValueError('Use an even RTP_START and at least 160 ports for 40 bridged calls.')
    carrier = []
    for text in v.get('HRINGDU_SIGNAL_CIDRS', '').split(','):
        if not text.strip():
            continue
        net = ipaddress.IPv4Network(text.strip(), strict=False)
        if net.prefixlen < 16 or not net.network_address.is_global:
            raise ValueError('Hringdu signalling entries must be public IPv4 CIDRs, /16 or narrower.')
        if any(net.overlaps(ipaddress.ip_network(t)) for t in TEAMS_NETS):
            raise ValueError('Hringdu and Teams signalling networks must not overlap.')
        carrier.append(str(net))
    if not carrier:
        raise ValueError('HRINGDU_SIGNAL_CIDRS is required; obtain the full list from Hringdu.')
    media = []
    for text in v.get('HRINGDU_MEDIA_CIDRS', '').split(','):
        if text.strip():
            net = ipaddress.IPv4Network(text.strip(), strict=False)
            if net.prefixlen < 16 or not net.network_address.is_global:
                raise ValueError('Hringdu media entries must be public IPv4 CIDRs, /16 or narrower.')
            media.append(str(net))
    if not media:
        raise ValueError('HRINGDU_MEDIA_CIDRS is required; obtain the full list from Hringdu.')
    v['CARRIER_SIGNAL_NETS'] = carrier
    v['CARRIER_MEDIA_NETS'] = media
    locals_ = []
    for text in v.get('LOCAL_NETS', '').split(','):
        if text.strip():
            net = ipaddress.IPv4Network(text.strip(), strict=False)
            if not any(net.subnet_of(ipaddress.ip_network(block)) for block in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
                raise ValueError('LOCAL_NETS must contain only actual private interface subnets.')
            locals_.append(str(net))
    # Config parser delimiters must be escaped, never interpolated as raw config.
    if not password or password != password.strip() or any(ord(c) < 32 or ord(c) > 126 for c in password):
        raise ValueError('Password must be one nonempty line of printable ASCII without edge spaces.')
    v['HRINGDU_PASSWORD'] = password.replace(';', r'\;')
    v['LOCAL_NET_LINES'] = '\n'.join('local_net=' + x for x in locals_)
    v['HRINGDU_MATCH_LINES'] = '\n'.join('match=' + x for x in carrier)
    v['TEAMS_PEERS'] = '\n'.join(
        f'[teams{i}](teams-base)\naors=teams{i}-aor\n\n'
        f'[teams{i}-aor]\ntype=aor\ncontact=sip:{host}:5061\\;transport=tls\n'
        f'qualify_frequency=60\nqualify_timeout=5\nqualify_2xx_only=yes\n'
        for i, host in enumerate(('sip.pstnhub.microsoft.com', 'sip2.pstnhub.microsoft.com', 'sip3.pstnhub.microsoft.com'), 1)
    )
    v['INBOUND_DID'] = (' same => n,Set(RAW_DID=${EXTEN})' if v['HRINGDU_DID_SOURCE'] == 'request_uri'
                        else ' same => n,Set(RAW_DID=${PJSIP_PARSE_URI(${PJSIP_HEADER(read,To)},user)})')
    v['OUTBOUND_PATTERN'] = (r'^[+][1-9][0-9]{6,14}$' if v['ALLOW_INTERNATIONAL'] == 'true' else r'^[+]354[0-9]{7}$')
    formats = {
        'national': ' same => n,Set(CARRIER_DEST=${DEST})\n same => n,ExecIf($[${REGEX("^[+]354[0-9]{7}$" ${DEST})}]?Set(CARRIER_DEST=${DEST:4}))\n same => n,ExecIf($[${REGEX("^[+]354[0-9]{7}$" ${DEST})} = 0]?Set(CARRIER_DEST=00${DEST:1}))',
        'e164': ' same => n,Set(CARRIER_DEST=${DEST})',
        'international': ' same => n,Set(CARRIER_DEST=00${DEST:1})',
    }
    v['FORMAT_DEST'] = formats[v['HRINGDU_DIAL_FORMAT']]
    v['FORMAT_CLI'] = '${CLI:4}' if v['HRINGDU_CLI_FORMAT'] == 'national' else '${CLI}'
    return v


def render(values, password, out):
    v = validate(values, password)
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o755)  # Parent .local is private; container UID must read this mounted child.
    for src in (ROOT / 'config').iterdir():
        if src.suffix == '.in':
            content = src.read_text()
            content = re.sub(r'@@([A-Z0-9_]+)@@', lambda m: v[m[1]], content)
            if re.search(r'@@[A-Z0-9_]+@@', content):
                raise ValueError('Unresolved template setting.')
            dest = out / src.stem
            dest.write_text(content)
        else:
            dest = out / src.name
            shutil.copyfile(src, dest)
        dest.chmod(0o644)
    (out / 'rtp.conf').write_text(f"[general]\nrtpstart={v['RTP_START']}\nrtpend={v['RTP_END']}\nstrictrtp=yes\nicesupport=no\n")
    firewall = f'''# Review before applying; changes only the hringbru table, preserves other host rules.
# nft >= 1.0.9; no SSH or default input policy changes.
# Remove the former project table when upgrading an existing installation.
destroy table inet sipsip
destroy table inet hringbru
table inet hringbru {{
  chain input {{
    type filter hook input priority -5; policy accept;
    iifname \"lo\" accept
    ip saddr {{ {', '.join(v['CARRIER_SIGNAL_NETS'])} }} {v['HRINGDU_TRANSPORT']} dport 5060 accept
    udp dport 5060 drop
    tcp dport 5060 drop
    ip saddr {{ {', '.join(TEAMS_NETS)} }} tcp dport 5061 accept
    tcp dport 5061 drop
    udp dport 5061 drop
    ip saddr {{ {', '.join((*TEAMS_NETS, *v['CARRIER_MEDIA_NETS']))} }} udp dport {v['RTP_START']}-{v['RTP_END']} accept
    udp dport {v['RTP_START']}-{v['RTP_END']} drop
  }}
}}
'''
    (out.parent / 'hringbru.nft').write_text(firewall)
    for name, content in {'http.conf': '[general]\nenabled=no\n', 'manager.conf': '[general]\nenabled=no\n',
                          'cdr.conf': '[general]\nenable=no\n', 'cel.conf': '[general]\nenable=no\n'}.items():
        (out / name).write_text(content)
    # Includes generated files written after the template loop (notably RTP).
    for dest in out.iterdir():
        if dest.is_file():
            dest.chmod(0o644)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--env', type=Path, default=ROOT / '.env')
    p.add_argument('--password-file', type=Path, default=ROOT / 'secrets/hringdu_password')
    p.add_argument('--output', type=Path, default=ROOT / '.local/asterisk')
    args = p.parse_args()
    try:
        os.umask(0o077)
        (ROOT / '.local').mkdir(mode=0o700, exist_ok=True)
        (ROOT / '.local').chmod(0o700)
        password = args.password_file.read_text().removesuffix('\n')
        render(read_env(args.env), password, args.output)
    except (ValueError, OSError, KeyError) as e:
        print(f'Configuration error: {e}', file=sys.stderr)
        return 1
    print('Configuration rendered. Password was not printed. Restart Asterisk to apply changes.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
