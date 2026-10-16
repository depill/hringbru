#!/usr/bin/env python3
"""Prompt locally for the trunk password without terminal echo or shell history."""
from getpass import getpass
import os
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.umask(0o077)
value = getpass('Hringdu SIP password: ')
if not value or value != value.strip() or any(ord(c) < 32 or ord(c) > 126 for c in value):
    raise SystemExit('Use a nonempty printable ASCII password without edge spaces.')
if value != getpass('Repeat password: '):
    raise SystemExit('Passwords did not match; no file was changed.')
folder = root / 'secrets'
folder.mkdir(mode=0o700, exist_ok=True)
folder.chmod(0o700)
path = folder / 'hringdu_password'
tmp = path.with_suffix('.new')
tmp.write_text(value + '\n')
tmp.chmod(0o600)
tmp.replace(path)
print('Password saved locally. Run scripts/render.py and restart Asterisk to apply.')
