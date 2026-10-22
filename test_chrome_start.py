import json
import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import chrome_start as launcher


class StartupTests(unittest.TestCase):
    def test_already_open_profile_preflight_is_successful_observation(self):
        busy = '123 chrome --user-data-dir=' + str(self.profile)
        with patch.object(launcher, 'read_processes', return_value=busy), patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.start(self.config)
        self.assertEqual(result['result'], 'BROWSER_ALREADY_OPEN')
        self.assertTrue(result['configuration_ready'])
        self.assertTrue(result['browser_open'])
        self.assertFalse(result['can_launch_new_instance'])
        self.assertFalse(result['automation_ready'])
        self.assertFalse(result['login_verified'])
        spawn.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'Application Support' / 'JobRobo' / 'ChromeProfile'
        self.profile.mkdir(parents=True)
        source = self.root / 'settings.py'
        # This exception proves configuration is parsed rather than imported.
        source.write_text('chrome_profile_path = ' + repr(str(self.profile)) + '\nraise RuntimeError("must never execute")\n')
        self.binary = self.root / 'Google Chrome'
        self.binary.write_text('test placeholder; never executed')
        self.binary.chmod(0o700)
        self.config = self.root / 'chrome_session.json'
        self.data = {'mode': 'read_only_no_send', 'browser': 'chrome',
                     'busy_profile_action': 'handoff',
                     'user_data_dir': str(self.profile), 'profile_directory': None,
                     'profile_source': str(source), 'profile_source_key': 'chrome_profile_path',
                     'session_strategy': 'open_if_idle_else_handoff',
                     'chrome_binary': str(self.binary), 'credentials': None,
                     'allowed_mutations': [], 'launch_second_instance': False, 'max_pages': 1}
        self.save()
        for name, value in [('ROOT', self.root), ('CHROME', self.binary)]:
            p = patch.object(launcher, name, value)
            p.start()
            self.addCleanup(p.stop)

    def save(self):
        self.config.write_text(json.dumps(self.data))

    def test_preflight_never_launches_and_preserves_spaces(self):
        with patch.object(launcher, 'read_processes', return_value=''), patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.start(self.config)
        spawn.assert_not_called()
        self.assertIn('--user-data-dir=' + str(self.profile.resolve()), result['launch_arguments'])
        self.assertFalse(result['login_verified'])

    def test_exact_active_profile_matching_and_other_profiles(self):
        for arg in [str(self.profile), '"' + str(self.profile) + '"', "'" + str(self.profile) + "'"]:
            text = '123 /Applications/Google Chrome --user-data-dir=' + arg + ' --new-window\n'
            self.assertEqual(launcher.active_profile_pids(self.profile, text), [123])
        self.assertEqual(launcher.active_profile_pids(self.profile, '999 chrome --user-data-dir=' + str(self.profile) + '-other'), [])
        alias = self.root / 'profile-alias'
        alias.symlink_to(self.profile)
        self.assertEqual(launcher.active_profile_pids(self.profile, '88 chrome --user-data-dir=' + str(alias)), [88])

    def test_lock_or_active_instance_never_launches_and_has_distinct_status(self):
        for busy in ['lock', 'process']:
            with self.subTest(busy=busy):
                lock = self.profile / 'SingletonLock'
                if busy == 'lock':
                    lock.symlink_to('/nonexistent-stale-lock')
                try:
                    text = '123 chrome --user-data-dir=' + str(self.profile) if busy == 'process' else ''
                    with patch.object(launcher, 'read_processes', return_value=text), patch.object(launcher.subprocess, 'Popen') as spawn:
                        result = launcher.start(self.config, True)
                        self.assertEqual(result['result'], 'PROFILE_LOCKED' if busy == 'lock' else 'BROWSER_ALREADY_OPEN')
                        self.assertTrue(result['configuration_ready'])
                        self.assertFalse(result['can_launch_new_instance'])
                        spawn.assert_not_called()
                        if busy == 'lock':
                            self.assertTrue(lock.is_symlink())
                finally:
                    if lock.is_symlink():
                        lock.unlink()

    def test_process_inspection_denial_fails_closed(self):
        with patch.object(launcher.subprocess, 'run', side_effect=PermissionError), patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.start(self.config, True)
            self.assertEqual(result['result'], 'PROFILE_STATE_UNKNOWN')
            self.assertIsNone(result['browser_open'])
            self.assertFalse(result['can_launch_new_instance'])
            spawn.assert_not_called()

    def test_config_mismatch_and_sending_are_rejected(self):
        for key, value in [('user_data_dir', str(self.root)), ('allowed_mutations', ['connect']),
                           ('credentials', 'not allowed'), ('launch_second_instance', True),
                           ('profile_directory', '../outside')]:
            original = self.data[key]
            self.data[key] = value
            self.save()
            with self.subTest(key=key), patch.object(launcher, 'read_processes', return_value=''):
                with self.assertRaises(launcher.StartupError):
                    launcher.start(self.config)
            self.data[key] = original
        self.save()

    def test_mocked_start_uses_no_shell_debug_port_or_login(self):
        child = Mock(pid=444)
        child.poll.return_value = None
        with patch.object(launcher.sys, 'platform', 'darwin'), patch.object(launcher, 'read_processes', return_value=''), patch.object(launcher.subprocess, 'Popen', return_value=child) as spawn, patch.object(launcher.time, 'sleep'):
            report = launcher.start(self.config, True)
        args, kwargs = spawn.call_args
        self.assertEqual(args[0][-1], 'https://www.linkedin.com/feed/')
        self.assertFalse(kwargs.get('shell', False))
        self.assertFalse(any('remote-debugging' in arg for arg in args[0]))
        self.assertFalse(report['login_verified'])
        self.assertEqual(report['outreach_actions'], 0)

    def test_profile_becomes_busy_between_checks(self):
        busy = '456 chrome --user-data-dir=' + str(self.profile)
        with patch.object(launcher.sys, 'platform', 'darwin'), patch.object(launcher, 'read_processes', side_effect=['', busy]), patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.start(self.config, True)
            self.assertEqual(result['result'], 'BROWSER_ALREADY_OPEN')
            spawn.assert_not_called()

    def test_immediate_exit_is_not_reported_success(self):
        child = Mock()
        child.poll.return_value = 1
        with patch.object(launcher.sys, 'platform', 'darwin'), patch.object(launcher, 'read_processes', return_value=''), patch.object(launcher.subprocess, 'Popen', return_value=child), patch.object(launcher.time, 'sleep'):
            with self.assertRaisesRegex(launcher.StartupError, 'exited immediately'):
                launcher.start(self.config, True)

    def test_missing_chrome_is_clear(self):
        self.binary.unlink()
        with self.assertRaisesRegex(launcher.StartupError, 'executable missing'):
            launcher.start(self.config)

    def test_concurrent_launcher_refuses_before_profile_check(self):
        with patch.object(launcher.fcntl, 'flock', side_effect=BlockingIOError), patch.object(launcher, 'check_ready') as check:
            with self.assertRaisesRegex(launcher.StartupError, 'Another ConnectRobo startup'):
                launcher.start(self.config, True)
            check.assert_not_called()

    def test_spawn_failure_is_clear_and_does_not_claim_login(self):
        with patch.object(launcher.sys, 'platform', 'darwin'), patch.object(launcher, 'read_processes', return_value=''), patch.object(launcher.subprocess, 'Popen', side_effect=OSError):
            with self.assertRaisesRegex(launcher.StartupError, 'failed to start'):
                launcher.start(self.config, True)

    def test_portable_profile_expands_home_without_jobrobo_checkout(self):
        self.data['profile_source'] = None
        self.data['user_data_dir'] = '~/Application Support/JobRobo/ChromeProfile'
        self.save()
        with patch.dict('os.environ', {'HOME': str(self.root)}), patch.object(launcher, 'read_processes', return_value=''):
            report = launcher.start(self.config)
        self.assertEqual(report['user_data_dir'], str(self.profile.resolve()))

    def test_clean_checkout_example_is_used_without_creating_local_config(self):
        import chrome_preflight
        module_path = self.root / 'chrome_preflight.py'
        example = self.root / 'chrome_session.example.json'
        self.config.unlink()
        example.write_text(json.dumps(self.data))
        with patch.object(chrome_preflight, '__file__', str(module_path)), patch.object(launcher, 'read_processes', return_value=''):
            self.assertTrue(launcher.start(self.config)['configuration_ready'])
        self.assertFalse(self.config.exists())

    def test_malformed_config_is_blocked_before_process_or_browser_access(self):
        for value in [[], {'mode': 'read_only_no_send'}, dict(self.data, user_data_dir=None)]:
            self.config.write_text(json.dumps(value))
            with self.subTest(value=type(value).__name__), patch.object(launcher, 'read_processes') as processes:
                with self.assertRaisesRegex(launcher.StartupError, 'Invalid configuration'):
                    launcher.start(self.config)
                processes.assert_not_called()

    def test_running_profile_with_locks_is_successful_preflight_and_start_handoff(self):
        lock = self.profile / 'SingletonLock'
        lock.symlink_to('/some-live-lock')
        busy = '123 chrome --user-data-dir=' + str(self.profile)
        for options in [[], ['--open']]:
            output = io.StringIO()
            with patch.object(launcher, 'read_processes', return_value=busy), patch.object(launcher.subprocess, 'Popen') as spawn, contextlib.redirect_stdout(output):
                self.assertEqual(launcher.main(['--config', str(self.config)] + options), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['result'], 'BROWSER_ALREADY_OPEN')
            self.assertTrue(report['browser_open'])
            self.assertFalse(report['automation_ready'])
            self.assertFalse(report['login_verified'])
            spawn.assert_not_called()
            self.assertTrue(lock.is_symlink())

    def test_lock_only_and_unknown_preflight_keep_nonzero_exit_without_invalid_config_claim(self):
        lock = self.profile / 'SingletonLock'
        lock.symlink_to('/stale-or-unobservable-lock')
        for state in ['locked', 'unknown']:
            output = io.StringIO()
            effect = launcher.StartupError('Cannot inspect') if state == 'unknown' else None
            with patch.object(launcher, 'read_processes', return_value='', side_effect=effect), patch.object(launcher.subprocess, 'Popen') as spawn, contextlib.redirect_stdout(output):
                self.assertEqual(launcher.main(['--config', str(self.config)]), 2)
            report = json.loads(output.getvalue())
            self.assertTrue(report['configuration_ready'])
            self.assertEqual(report['result'], 'PROFILE_STATE_UNKNOWN' if state == 'unknown' else 'PROFILE_LOCKED')
            self.assertFalse(report['can_launch_new_instance'])
            self.assertFalse(report['login_verified'])
            spawn.assert_not_called()
            self.assertTrue(lock.is_symlink())


if __name__ == '__main__':
    unittest.main()
