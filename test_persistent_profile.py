import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import chrome_start
import connectrobo
import selenium_controller


class PersistentProfileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.profile = self.root / 'Application Support/ConnectRobo/ChromeProfile'
        self.binary = self.root / 'Chrome'
        self.binary.touch(mode=0o700)
        self.config = self.root / 'chrome_session.json'
        self.data = json.loads(Path('chrome_session.example.json').read_text())
        self.data.update(user_data_dir=str(self.profile), chrome_binary=str(self.binary),
                         profile_kind='connectrobo_persistent', create_profile_if_missing=True,
                         profile_source=None, busy_profile_action='handoff')
        self.config.write_text(json.dumps(self.data))
        for name, value in [('ROOT', self.root), ('CHROME', self.binary)]:
            setting = patch.object(chrome_start, name, value)
            setting.start()
            self.addCleanup(setting.stop)

    def test_default_is_independent_persistent_profile(self):
        data = json.loads(Path('chrome_session.example.json').read_text())
        self.assertEqual(data['user_data_dir'], '~/Library/Application Support/ConnectRobo/ChromeProfile')
        self.assertEqual(data['busy_profile_action'], 'handoff')
        self.assertTrue(data['create_profile_if_missing'])
        self.assertIsNone(data['profile_source'])

    def test_preflight_accepts_new_profile_without_creating_it(self):
        with patch.object(chrome_start, 'read_processes', return_value=''), patch.object(chrome_start.subprocess, 'Popen') as spawn:
            report = chrome_start.start(self.config)
        self.assertEqual(report['result'], 'PREFLIGHT_PASS')
        self.assertEqual(report['profile_kind'], 'connectrobo_persistent')
        self.assertFalse(self.profile.exists())
        spawn.assert_not_called()

    def test_start_creates_then_reuses_profile_while_jobrobo_is_open(self):
        child = Mock(pid=444)
        child.poll.return_value = None
        jobrobo = '123 chrome --user-data-dir=' + str(self.root / 'Application Support/JobRobo/ChromeProfile')
        with patch.object(chrome_start, 'read_processes', return_value=jobrobo), patch.object(chrome_start.subprocess, 'Popen', return_value=child) as spawn, patch.object(chrome_start.time, 'sleep'):
            chrome_start.start(self.config, True)
            marker = self.profile / 'saved-profile-marker'
            marker.write_text('preserve')
            chrome_start.start(self.config, True)
        self.assertEqual(marker.read_text(), 'preserve')
        self.assertEqual(spawn.call_count, 2)
        for call in spawn.call_args_list:
            self.assertIn('--user-data-dir=' + str(self.profile), call.args[0])
            self.assertNotIn('--incognito', call.args[0])

    def test_busy_persistent_profile_never_falls_back(self):
        self.profile.mkdir(parents=True)
        self.data['busy_profile_action'] = 'private_fallback'
        self.config.write_text(json.dumps(self.data))
        busy = '123 chrome --user-data-dir=' + str(self.profile)
        child = Mock()
        child.wait.return_value = 0
        with patch.object(chrome_start, 'read_processes', return_value=busy), patch.object(chrome_start.subprocess, 'Popen', return_value=child) as spawn:
            report = chrome_start.start(self.config, True)
        self.assertEqual(report['result'], 'CHROME_PROFILE_REUSED')
        self.assertTrue(report['browser_reused'])
        self.assertFalse(report['browser_launched'])
        self.assertFalse(report['login_verified'])
        self.assertFalse(report['fallback_proposed'])
        arguments = spawn.call_args.args[0]
        self.assertIn('--user-data-dir=' + str(self.profile), arguments)
        self.assertEqual(arguments[-1], 'https://www.linkedin.com/feed/')
        self.assertNotIn('--new-window', arguments)
        self.assertFalse(spawn.call_args.kwargs.get('shell', False))

    def test_busy_persistent_preflight_does_not_open_a_tab(self):
        self.profile.mkdir(parents=True)
        busy = '123 chrome --user-data-dir=' + str(self.profile)
        with patch.object(chrome_start, 'read_processes', return_value=busy), patch.object(chrome_start.subprocess, 'Popen') as spawn:
            report = chrome_start.start(self.config)
        self.assertEqual(report['result'], 'BROWSER_ALREADY_OPEN')
        spawn.assert_not_called()

    def test_persistent_profile_becoming_busy_is_reused(self):
        self.profile.mkdir(parents=True)
        busy = '123 chrome --user-data-dir=' + str(self.profile)
        child = Mock()
        child.wait.return_value = 0
        with patch.object(chrome_start, 'read_processes', side_effect=['', busy]), patch.object(chrome_start.subprocess, 'Popen', return_value=child):
            report = chrome_start.start(self.config, True)
        self.assertEqual(report['result'], 'CHROME_PROFILE_REUSED')

    def test_failed_or_pending_reuse_never_claims_success_or_kills_chrome(self):
        self.profile.mkdir(parents=True)
        busy = '123 chrome --user-data-dir=' + str(self.profile)
        for outcome in ('failed', 'pending', 'spawn_error'):
            with self.subTest(outcome=outcome):
                child = Mock()
                child.wait.return_value = 1
                if outcome == 'pending':
                    child.wait.side_effect = subprocess.TimeoutExpired('Chrome', 5)
                error = OSError if outcome == 'spawn_error' else None
                with patch.object(chrome_start, 'read_processes', return_value=busy), patch.object(chrome_start.subprocess, 'Popen', return_value=child, side_effect=error):
                    with self.assertRaises(chrome_start.StartupError):
                        chrome_start.start(self.config, True)
                child.kill.assert_not_called()
                child.terminate.assert_not_called()

    def test_persistent_locked_or_unobservable_profile_never_opens(self):
        self.profile.mkdir(parents=True)
        lock = self.profile / 'SingletonLock'
        lock.symlink_to('/unobservable-lock')
        for state in ('locked', 'unknown'):
            error = chrome_start.StartupError('Cannot inspect') if state == 'unknown' else None
            with patch.object(chrome_start, 'read_processes', return_value='', side_effect=error), patch.object(chrome_start.subprocess, 'Popen') as spawn:
                report = chrome_start.start(self.config, True)
            self.assertEqual(report['result'], 'PROFILE_LOCKED' if state == 'locked' else 'PROFILE_STATE_UNKNOWN')
            self.assertTrue(lock.is_symlink())
            spawn.assert_not_called()

    def test_missing_legacy_profile_still_fails_closed(self):
        self.data.pop('profile_kind')
        self.config.write_text(json.dumps(self.data))
        with self.assertRaises(chrome_start.StartupError):
            chrome_start.start(self.config)
        self.assertFalse(self.profile.exists())

    def selenium_modules(self):
        self.options = Mock()
        self.webdriver = Mock()
        return {'selenium': types.SimpleNamespace(webdriver=self.webdriver),
                'selenium.webdriver': self.webdriver,
                'selenium.webdriver.chrome': types.SimpleNamespace(),
                'selenium.webdriver.chrome.options': types.SimpleNamespace(Options=Mock(return_value=self.options))}

    def test_selenium_reuses_configured_profile_without_incognito(self):
        with patch.dict('sys.modules', self.selenium_modules()), patch.object(chrome_start, 'read_processes', return_value=''):
            for _ in range(2):
                controller = selenium_controller.create_controller(self.root, self.config)
                controller.driver.quit()
        arguments = [call.args[0] for call in self.options.add_argument.call_args_list]
        self.assertEqual(arguments.count('--user-data-dir=' + str(self.profile)), 2)
        self.assertNotIn('--incognito', arguments)
        self.assertTrue(self.profile.is_dir())
        self.webdriver.Chrome.return_value.get.assert_called_with('https://www.linkedin.com/feed/')

    def test_selenium_busy_or_locked_profile_blocks_before_driver_creation(self):
        self.profile.mkdir(parents=True)
        for state in ('busy', 'locked', 'unknown'):
            with self.subTest(state=state):
                lock = self.profile / 'SingletonLock'
                if state == 'locked':
                    lock.symlink_to('/unobservable-lock')
                try:
                    busy = '123 chrome --user-data-dir=' + str(self.profile) if state == 'busy' else ''
                    error = chrome_start.StartupError('Cannot inspect') if state == 'unknown' else None
                    with patch.dict('sys.modules', self.selenium_modules()), patch.object(chrome_start, 'read_processes', return_value=busy, side_effect=error):
                        with self.assertRaises(selenium_controller.ControlError):
                            selenium_controller.create_controller(self.root, self.config)
                    self.webdriver.Chrome.assert_not_called()
                finally:
                    if lock.is_symlink():
                        lock.unlink()

    def test_selenium_restart_retains_profile_and_waits_before_creating_driver(self):
        import selenium_controller
        self.profile.mkdir(parents=True)
        marker = self.profile / 'saved-profile-marker'
        marker.write_text('preserve')
        busy = '123 chrome --user-data-dir=' + str(self.profile)
        modules = self.selenium_modules()
        with patch.dict('sys.modules', modules), patch.object(chrome_start, 'read_processes', side_effect=[busy, '']), patch.object(chrome_start, 'request_profile_close', return_value=123) as close, patch.object(selenium_controller.time, 'sleep'), contextlib.redirect_stderr(io.StringIO()):
            close.side_effect = lambda state: self.webdriver.Chrome.assert_not_called() or 123
            selenium_controller.create_controller(self.root, self.config, profile_timeout=10, restart_browser=True)
        close.assert_called_once()
        self.webdriver.Chrome.assert_called_once()
        arguments = [call.args[0] for call in self.options.add_argument.call_args_list]
        self.assertIn('--user-data-dir=' + str(self.profile), arguments)
        self.assertNotIn('--incognito', arguments)
        self.assertEqual(marker.read_text(), 'preserve')

    def test_cli_passes_custom_config_to_selenium(self):
        with patch.object(selenium_controller, 'create_controller') as create, patch.object(selenium_controller, 'wait_for_manual_login'), patch.object(connectrobo, 'run_connections', return_value={'result': 'CANDIDATES_EXHAUSTED'}), contextlib.redirect_stdout(io.StringIO()):
            connectrobo.main(['--connect-once', '--controller', 'selenium', '--run-id', 'fixture', '--config', str(self.config)])
        self.assertEqual(create.call_args.args[1], self.config)
