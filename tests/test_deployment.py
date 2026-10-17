"""Exercise deployment safety without SSH, nft writes, Docker operations or ACME."""
from contextlib import redirect_stdout
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location('deploy_server', ROOT / 'scripts/deploy-server.py')
d = importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
loader = SourceFileLoader('deploy_cli', str(ROOT / 'bin/deploy'))
spec_cli = importlib.util.spec_from_loader(loader.name, loader)
cli = importlib.util.module_from_spec(spec_cli); loader.exec_module(cli)


class InventoryTest(unittest.TestCase):
    def validate(self, payload):
        with patch.object(cli.shutil, 'which', return_value='/fake/ansible-inventory'), \
             patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, payload, '')):
            cli.validate_inventory(Path('/fake/inventory.yml'))

    def test_no_matching_hosts_cannot_report_success(self):
        with self.assertRaisesRegex(ValueError, 'exactly one host'): self.validate('{"all": {}}')

    def test_multiple_targets_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'exactly one host'):
            self.validate('{"hringbru": {"hosts": ["one", "two"]}}')

    def test_one_target_in_a_child_group_is_valid(self):
        self.validate('{"hringbru": {"children": ["production"]}, "production": {"hosts": ["bridge"]}}')


class DeploymentModesTest(unittest.TestCase):
    def invoke(self, flags):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inventory = root / 'deploy/ansible/inventory.yml'
            inventory.parent.mkdir(parents=True); inventory.write_text('placeholder')
            # No .env, password, or scripts directory exists in this fixture.
            with patch.object(cli, 'ROOT', root), patch.object(cli, 'payload_files', return_value=[]), \
                 patch.object(cli, 'validate_inventory'), patch.object(cli.shutil, 'which', return_value='/mock/ansible-playbook'), \
                 patch.object(cli.sys, 'argv', ['deploy', *flags]), \
                 patch.object(cli.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as execute, \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 0)
                return execute.call_args.args[0]

    def test_audit_needs_no_sip_settings_or_password_prompt(self):
        command = self.invoke(['--check-server'])
        self.assertTrue(any(x.endswith('/check-server.yml') for x in command))
        self.assertNotIn('--ask-become-pass', command)

    def test_bootstrap_uses_separate_playbook_and_prompts_for_root_password(self):
        command = self.invoke(['--bootstrap'])
        self.assertTrue(any(x.endswith('/bootstrap.yml') for x in command))
        self.assertIn('--ask-become-pass', command)

    def test_bootstrap_syntax_check_never_prompts(self):
        command = self.invoke(['--bootstrap', '--syntax-check'])
        self.assertIn('--syntax-check', command)
        self.assertNotIn('--ask-become-pass', command)


class DeploymentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.release = self.base / 'releases' / ('a' * 64)
        self.shared = self.base / 'shared'
        self.shared.mkdir(); self.release.mkdir(parents=True)
        (self.release / 'secrets').mkdir()
        (self.release / '.local').symlink_to(self.shared)
        values = d.read_env(ROOT / '.env.example')
        values.update(DID_NUMBERS=','.join('+354'+str(n) for n in range(5550100,5550120)), ASTERISK_IMAGE='test:sha-'+'a'*40, SBC_FQDN='sbc.example.net', HRINGDU_HOST='sip.provider.example',
                      HRINGDU_USERNAME='00000000*0001', HRINGDU_CONTACT_USER='00000000*0001',
                      HRINGDU_FROM_USER='00000000*0001', PUBLIC_IPV4='8.8.8.8', HRINGDU_SIGNAL_CIDRS='9.9.9.9/32', HRINGDU_MEDIA_CIDRS='9.9.9.0/24', ACME_EMAIL='ops@example.is')
        (self.release / '.env').write_text(''.join(f'{k}={v}\n' for k,v in values.items()))
        (self.release / 'secrets/hringdu_password').write_text('offline-secret-only\n')
        self.commands = []; self.running = False; self.active = 0; self.fail_health = False; self.projects = {}

    def tearDown(self): self.temp.cleanup()

    def fake_run(self, args, *, capture=False):
        self.commands.append(args)
        output = ''
        if args[:3] == ['docker', 'compose', '--project-directory']:
            command = args[4:]
            if command[:1] == ['--project-name']: command = command[2:]
            if command[:1] == ['ps']: output = 'test-container\n' if self.running else ''
            elif command[:1] == ['exec']: output = f'{self.active} active channels\n0 active calls\n'
            elif command[:1] == ['config']: output = json.dumps({'name': self.projects.get(str(Path(args[3]).resolve()), 'hringbru'), 'services': {'asterisk': {'image': 'image-1', 'build': '.'}}})
            elif command[:1] == ['stop']: self.running = False
            elif command[:1] == ['up']:
                if self.fail_health: raise subprocess.CalledProcessError(1, args)
                self.running = True
        elif args[:2] == ['docker', 'inspect']:
            output = json.dumps([{'Image': 'image-1', 'State': {'Running': self.running, 'Health': {'Status': 'healthy'}}}])
        elif args[:3] == ['docker', 'image', 'inspect']: output = 'image-1\n'
        return subprocess.CompletedProcess(args, 0, output, '')

    def apply(self, addresses=None):
        addresses = addresses or [(None, None, None, None, ('8.8.8.8', 0))]
        with patch.object(d, 'ROOT', self.release), patch.object(d.os, 'geteuid', return_value=0), \
             patch.object(d.socket, 'getaddrinfo', return_value=addresses), patch.object(d, 'certificate_ready', return_value=True), \
             patch.object(d, 'run', side_effect=self.fake_run), redirect_stdout(io.StringIO()):
            d.main()

    def test_first_deploy_then_noop_preserves_runtime_and_does_not_restart(self):
        self.apply()
        self.assertEqual((self.base / 'current').resolve(), self.release.resolve())
        marker = self.shared / 'deployed-version'
        self.assertEqual(marker.read_text().strip(), self.release.name)
        self.assertTrue((self.shared / 'asterisk/pjsip.conf').exists())
        self.assertIn(['/usr/bin/python3', str(self.release / 'scripts/certificates.py'), 'install'], self.commands)
        first_up = next(c for c in self.commands if 'up' in c)
        self.assertIn('--force-recreate', first_up)
        firewall_index = next(i for i,c in enumerate(self.commands) if c[:3] == ['nft', '-f', str(self.shared / 'hringbru.nft')])
        self.assertLess(firewall_index, self.commands.index(first_up))
        config = self.shared / 'asterisk/pjsip.conf'; before = config.stat().st_mtime_ns
        self.commands.clear(); self.active = 2
        self.apply()  # An unchanged healthy deployment does not interrupt existing calls.
        self.assertFalse(any('up' in c for c in self.commands))
        self.assertEqual(config.stat().st_mtime_ns, before)

    def test_active_call_rejection_leaves_running_config_and_success_marker_untouched(self):
        self.running = True; self.active = 1
        marker = self.shared / 'deployed-version'; marker.write_text('previous\n')
        with self.assertRaisesRegex(ValueError, 'Active calls detected'): self.apply()
        self.assertEqual(marker.read_text(), 'previous\n')
        self.assertFalse((self.shared / 'asterisk').exists())
        self.assertFalse(any('up' in c for c in self.commands))
        self.assertFalse(any(c[:2] == ['nft', '-f'] for c in self.commands))

    def previous_release(self, project_setting=None):
        old = self.base / 'releases' / ('b' * 64)
        old.mkdir()
        (old / '.env').write_text('' if project_setting is None else 'HRINGBRU_PROJECT_NAME=' + project_setting + '\n')
        (self.base / 'current').symlink_to(old)
        return old

    def test_legacy_missing_env_setting_uses_compose_identity(self):
        old = self.previous_release()  # Legacy Compose hardcoded its project name.
        env = self.release / '.env'
        env.write_text(env.read_text().replace('HRINGBRU_PROJECT_NAME=hringbru', 'HRINGBRU_PROJECT_NAME=sipsip'))
        for release in (old, self.release): self.projects[str(release.resolve())] = 'sipsip'
        self.apply()
        self.assertEqual((self.base / 'current').resolve(), self.release.resolve())
        self.assertTrue(any('pull' in c for c in self.commands))

    def test_real_project_change_is_rejected_before_pull_or_shared_mutation(self):
        old = self.previous_release()
        self.projects[str(old.resolve())] = 'sipsip'
        with self.assertRaisesRegex(ValueError, 'cannot change from sipsip to hringbru'): self.apply()
        self.assertEqual((self.base / 'current').resolve(), old.resolve())
        self.assertFalse((self.shared / 'asterisk').exists())
        self.assertFalse(any('pull' in c or 'up' in c for c in self.commands))
        self.assertFalse(any(c[:2] == ['nft', '-f'] for c in self.commands))

    def test_existing_custom_project_uses_compose_precedence(self):
        old = self.previous_release('custom-project')
        # Includes COMPOSE_PROJECT_NAME / fixed `name:` precedence, not just .env.
        for release in (old, self.release): self.projects[str(release.resolve())] = 'custom-project'
        self.apply()
        self.assertEqual((self.base / 'current').resolve(), self.release.resolve())

    def test_failed_health_check_does_not_promote_release(self):
        self.fail_health = True
        with self.assertRaises(subprocess.CalledProcessError): self.apply()
        self.assertFalse((self.base / 'current').exists())
        self.assertFalse((self.shared / 'deployed-version').exists())

    def test_failed_update_restores_exact_previous_configuration_image_and_marker(self):
        self.apply()
        old_release = self.base / 'releases' / ('b' * 64)
        self.release.rename(old_release)
        (self.base / 'current').unlink(); (self.base / 'current').symlink_to(old_release)
        self.release.mkdir(); (self.release / 'secrets').mkdir()
        for name in ['.env', 'secrets/hringdu_password']:
            (self.release / name).write_bytes((old_release / name).read_bytes())
        (self.release / '.local').symlink_to(self.shared)
        marker = self.shared / 'deployed-version'; marker.write_text(old_release.name + '\n')
        config = self.shared / 'asterisk/pjsip.conf'
        config.write_text('previous-private-configuration')
        self.fail_health = True
        with self.assertRaises(subprocess.CalledProcessError): self.apply()
        self.assertEqual(config.read_text(), 'previous-private-configuration')
        self.assertEqual(marker.read_text().strip(), old_release.name)
        self.assertEqual((self.base / 'current').resolve(), old_release.resolve())
        runtime = json.loads((self.shared / 'rollback/runtime.json').read_text())
        self.assertEqual(runtime['services']['asterisk']['image'], 'image-1')
        self.assertNotIn('build', runtime['services']['asterisk'])
        self.assertFalse((self.shared / 'deployment-pending.json').exists())
        self.assertTrue(any('runtime.json' in str(c) and 'up' in c for c in self.commands))

    def test_interrupted_deployment_retains_snapshot_when_calls_block_recovery(self):
        self.apply()
        transaction = d.Transaction(self.base)
        with patch.object(d, 'ROOT', self.release): transaction.begin(None, None)
        self.running = True; self.active = 1
        with self.assertRaisesRegex(ValueError, 'Active calls detected'): self.apply()
        self.assertTrue(transaction.pending.exists())
        self.assertTrue((transaction.path / 'asterisk/pjsip.conf').exists())

    def test_wrong_dns_prevents_build_and_firewall_changes(self):
        with self.assertRaisesRegex(ValueError, 'DNS-only A record'):
            self.apply([(None, None, None, None, ('1.1.1.1', 0))])
        self.assertEqual(self.commands, [])

    def test_unparseable_call_count_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'Cannot determine'): d.check_idle('CLI unavailable')


if __name__ == '__main__': unittest.main()
