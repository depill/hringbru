"""Incident debounce, recovery and safe webhook retry semantics."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('status', ROOT / 'scripts/status.py')
s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)


class MonitoringTest(unittest.TestCase):
    def test_transient_failure_debounce_and_recovery(self):
        state = {}; bad = {'status': 'critical'}; good = {'status': 'ok'}
        self.assertIsNone(s.transition(state, bad, 100))
        self.assertIsNone(s.transition(state, good, 101))
        for now in [102, 103]: self.assertIsNone(s.transition(state, bad, now))
        self.assertEqual(s.transition(state, bad, 104), 'incident')
        state.update(incident=True, last_sent=104)
        self.assertIsNone(s.transition(state, bad, 105))
        self.assertEqual(s.transition(state, bad, 21704), 'reminder')
        self.assertIsNone(s.transition(state, good, 21705))
        self.assertEqual(s.transition(state, good, 21706), 'recovery')

    def test_failed_delivery_retries_and_does_not_mark_sent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / '.local').mkdir()
            with patch.object(s, 'deliver', return_value=False) as deliver:
                for _ in range(4): s.monitor(root, {'status': 'critical'})
            self.assertEqual(deliver.call_count, 2)
            state = json.loads((root / '.local/monitor-state.json').read_text())
            self.assertFalse(state.get('incident'))
            self.assertNotIn('last_sent', state)
            self.assertEqual((root / '.local/monitor-state.json').stat().st_mode & 0o777, 0o600)

    def test_cli_parsing_rejects_unavailable_peers_and_unregistered_carrier(self):
        from collections import namedtuple
        usage = namedtuple('Usage', 'total used free')(10000000000, 1, 9000000000)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root / '.env').write_text('SBC_FQDN=sbc.example.net\n')
            def command(args):
                if args[:2] == ['docker', 'inspect']: return '[{"State":{"Running":true,"Health":{"Status":"healthy"}}}]'
                if 'ps' in args: return 'container'
                if args[-1] == 'pjsip show registrations': return 'hringdu-registration/sip:provider:5060 hringdu-auth Rejected'
                if args[-1] == 'pjsip show contacts': return 'Contact: teams1-aor/sip:provider:5061 hash Unavail nan'
                if '-enddate' in args: return 'notAfter=Oct 16 12:00:00 2030 GMT'
                if 'show' in args: return 'success'
                return ''
            with patch.object(s, 'command', side_effect=command), patch.object(s.shutil, 'disk_usage', return_value=usage):
                report = s.collect(root, 'hringbru')
            self.assertEqual(report['status'], 'critical')
            self.assertEqual(report['checks']['teams_peers_available'], 0)
            self.assertEqual(report['checks']['registration'], 'not registered')


if __name__ == '__main__': unittest.main()
