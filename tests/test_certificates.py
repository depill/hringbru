"""Verify mounted TLS permissions with the same private umask used on the server."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('certificates', ROOT / 'scripts/certificates.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class CertificatePermissionsTest(unittest.TestCase):
    def test_first_install_and_unchanged_install_repair_mounted_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            local = root / '.local'
            local.mkdir(mode=0o700)
            source = local / 'letsencrypt/live/sbc.example.net'
            source.mkdir(parents=True)
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '7',
                            '-subj', '/CN=sbc.example.net', '-keyout', str(source / 'privkey.pem'),
                            '-out', str(source / 'fullchain.pem')], check=True, capture_output=True)
            previous = os.umask(0o077)
            try:
                with patch.object(c, 'ROOT', root), patch.object(c, 'compose',
                        return_value=subprocess.CompletedProcess([], 0, '')) as compose:
                    c.install('sbc.example.net')
                    target = local / 'tls'
                    self.assertEqual(target.stat().st_mode & 0o777, 0o755)
                    for path in target.iterdir():
                        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                        path.chmod(0o600)
                    target.chmod(0o700)
                    compose.reset_mock()
                    c.install('sbc.example.net')
                    self.assertEqual(target.stat().st_mode & 0o777, 0o755)
                    for path in target.iterdir():
                        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                    compose.assert_not_called()  # Permission repair does not restart Asterisk.
                    self.assertEqual(local.stat().st_mode & 0o777, 0o700)
            finally:
                os.umask(previous)


if __name__ == '__main__': unittest.main()
