"""Meaningful checks for config injection, source ACLs and credential handling."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render', ROOT / 'scripts/render.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def settings():
    v = r.read_env(ROOT / '.env.example')
    v.update(DID_NUMBERS=','.join('+354'+str(n) for n in range(5550100,5550120)), SBC_FQDN='sbc.example.net', PUBLIC_IPV4='8.8.8.8', LOCAL_NETS='10.0.0.0/24',
             HRINGDU_HOST='sip.provider.example', HRINGDU_USERNAME='00000000*0001',
             HRINGDU_CONTACT_USER='00000000*0001', HRINGDU_FROM_USER='00000000*0001',
             HRINGDU_SIGNAL_CIDRS='9.9.9.9/32', HRINGDU_MEDIA_CIDRS='9.9.9.0/24')
    return v


class ConfigurationTest(unittest.TestCase):
    def test_all_mounted_configuration_is_readable_under_private_umask(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'asterisk'
            previous = os.umask(0o077)
            try:
                r.render(settings(), 'test-only', out)
            finally:
                os.umask(previous)
            self.assertEqual(out.stat().st_mode & 0o777, 0o755)
            for path in out.iterdir():
                with self.subTest(file=path.name):
                    self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_missing_carrier_sources_cannot_render(self):
        for field in ('HRINGDU_SIGNAL_CIDRS', 'HRINGDU_MEDIA_CIDRS'):
            with self.subTest(field=field):
                v = settings(); v[field] = ''
                with self.assertRaises(ValueError): r.validate(v, 'test-only')

    def test_config_injection_and_broad_trust_rejected(self):
        for field, value in (('SBC_FQDN', 'host\n[evil]'), ('HRINGDU_USERNAME', 'x;injection'),
                             ('HRINGDU_SIGNAL_CIDRS', '0.0.0.0/0'), ('LOCAL_NETS', '0.0.0.0/0'),
                             ('HRINGDU_SIGNAL_CIDRS', '52.112.0.0/14'), ('PUBLIC_IPV4', '192.168.1.2')):
            with self.subTest(field=field, value=value):
                v = settings(); v[field] = value
                with self.assertRaises(ValueError): r.validate(v, 'test-only')
        for password in ('abc\n[evil]', 'a\rb', '', ' secret '):
            with self.assertRaises(ValueError): r.validate(settings(), password)

    def test_explicit_noncontiguous_allowlist_and_invalid_numbers(self):
        v = settings(); v['DID_NUMBERS'] = '+3545550700,+3545550912'
        pattern = r.validate(v, 'test-only')['DID_PATTERN']
        import re
        self.assertTrue(re.fullmatch(pattern, '+3545550700'))
        self.assertTrue(re.fullmatch(pattern, '+3545550912'))
        self.assertFalse(re.fullmatch(pattern, '+3545550701'))
        for numbers in ['', '+3545550700,+3545550700', '+442079460000', '+3545550700|.*']:
            v['DID_NUMBERS'] = numbers
            with self.assertRaises(ValueError): r.validate(v, 'test-only')

    def test_password_delimiters_and_backslashes_preserved(self):
        self.assertEqual(r.validate(settings(), r'a\b;cd')['HRINGDU_PASSWORD'], r'a\b\;cd')

    def test_rendered_trust_and_tls(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'asterisk'
            r.render(settings(), 'test-only', out)
            sip = (out / 'pjsip.conf').read_text()
            self.assertIn('client_uri=sip:00000000*0001@sip.provider.example:5060', sip)
            self.assertIn('external_signaling_hostname=sbc.example.net', sip)
            self.assertIn('verify_client=yes', sip)
            self.assertIn('require_client_cert=yes', sip)
            self.assertIn('verify_server=yes', sip)
            self.assertNotIn('@@', sip)
            self.assertIn(r'5061\;transport=tls', sip)
            self.assertIn('endpoint_identifier_order=ip', sip)
            self.assertNotIn('type=anonymous', sip)
            self.assertIn('udp dport 5060 drop', (out.parent / 'hringbru.nft').read_text())


if __name__ == '__main__': unittest.main()
