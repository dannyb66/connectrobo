import contextlib
import errno
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

import chrome_start
from selenium_controller import wait_for_profile_idle


class StaleProfileLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.lock = self.profile / 'SingletonLock'
        self.lock.symlink_to('test-host-424242')
        self.endpoint = self.root / 's'
        (self.profile / 'SingletonSocket').symlink_to(self.endpoint)
        (self.profile / 'SingletonCookie').symlink_to('synthetic-singleton-token')
        self.binary = self.root / 'Chrome'
        self.binary.touch(mode=0o700)
        self.data = json.loads(Path('chrome_session.example.json').read_text())
        self.data.update(user_data_dir=str(self.profile), chrome_binary=str(self.binary))
        self.config = self.root / 'chrome_session.json'
        self.config.write_text(json.dumps(self.data))
        for target, value in [('CHROME', self.binary), ('ROOT', self.root)]:
            setting = patch.object(chrome_start, target, value)
            setting.start()
            self.addCleanup(setting.stop)
        hostname = patch.object(chrome_start.socket, 'gethostname', return_value='test-host')
        hostname.start()
        self.addCleanup(hostname.stop)

    def test_dead_local_owner_and_missing_socket_allow_native_chrome_recovery(self):
        before = {p.name: p.readlink() for p in self.profile.iterdir()}
        with patch.object(chrome_start, 'read_processes', return_value=''), patch.object(chrome_start.os, 'kill', side_effect=ProcessLookupError) as probe, patch.object(chrome_start.subprocess, 'Popen') as launch:
            ready = chrome_start.start(self.config)
        self.assertEqual(ready['result'], 'PROFILE_RECOVERABLE')
        self.assertEqual(ready['profile_state'], 'stale_lock')
        self.assertTrue(ready['can_launch_new_instance'])
        self.assertTrue(ready['stale_lock_recovery'])
        self.assertEqual(before, {p.name: p.readlink() for p in self.profile.iterdir()})
        self.assertTrue(all(call.args == (424242, 0) for call in probe.call_args_list))
        launch.assert_not_called()

    def test_dead_local_owner_and_refused_socket_are_recoverable(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.endpoint))
        with patch.object(chrome_start.os, 'kill', side_effect=ProcessLookupError):
            self.assertTrue(chrome_start.stale_profile_lock(self.profile))

    def test_listening_socket_blocks_recovery_even_if_owner_pid_is_gone(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.endpoint))
            listener.listen(1)
            with patch.object(chrome_start.os, 'kill', side_effect=ProcessLookupError):
                self.assertFalse(chrome_start.stale_profile_lock(self.profile))

    def test_live_or_unobservable_owner_blocks_recovery(self):
        for outcome in (None, PermissionError('denied'), OSError('unknown')):
            with self.subTest(outcome=outcome), patch.object(chrome_start.os, 'kill', side_effect=outcome):
                self.assertFalse(chrome_start.stale_profile_lock(self.profile))

    def test_foreign_host_or_malformed_lock_is_preserved(self):
        for target in ('other-host-424242', 'test-host-invalid', 'test-host-0', 'test-host-1'):
            self.lock.unlink()
            self.lock.symlink_to(target)
            with self.subTest(target=target), patch.object(chrome_start.os, 'kill') as probe:
                self.assertFalse(chrome_start.stale_profile_lock(self.profile))
            probe.assert_not_called()
            self.assertEqual(str(self.lock.readlink()), target)

    def test_socket_permission_or_timeout_does_not_prove_staleness(self):
        for error in (PermissionError(errno.EACCES, 'denied'), TimeoutError()):
            with self.subTest(error=error), patch.object(chrome_start.os, 'kill', side_effect=ProcessLookupError), patch.object(chrome_start.socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.connect.side_effect = error
                self.assertFalse(chrome_start.stale_profile_lock(self.profile))

    def test_owner_becoming_alive_or_lock_changing_blocks_recovery(self):
        with patch.object(chrome_start.os, 'kill', side_effect=[ProcessLookupError(), None]):
            self.assertFalse(chrome_start.stale_profile_lock(self.profile))
        with patch.object(chrome_start.os, 'kill', side_effect=ProcessLookupError), patch.object(chrome_start.os, 'readlink', side_effect=['test-host-424242', str(self.endpoint), 'test-host-9']):
            self.assertFalse(chrome_start.stale_profile_lock(self.profile))

    def test_shared_profile_process_denial_and_running_profile_remain_blocked(self):
        self.data['profile_kind'] = 'configured_shared'
        self.config.write_text(json.dumps(self.data))
        with patch.object(chrome_start, 'read_processes', return_value=''), patch.object(chrome_start, 'stale_profile_lock') as recovery:
            self.assertEqual(chrome_start.check_ready(self.config)['result'], 'PROFILE_LOCKED')
        recovery.assert_not_called()
        self.data['profile_kind'] = 'connectrobo_persistent'
        self.config.write_text(json.dumps(self.data))
        busy = '123 ' + str(self.binary) + ' --user-data-dir=' + str(self.profile)
        for process_text, error, expected in [(busy, None, 'BROWSER_ALREADY_OPEN'), ('', chrome_start.StartupError('Cannot inspect'), 'PROFILE_STATE_UNKNOWN')]:
            with patch.object(chrome_start, 'read_processes', return_value=process_text, side_effect=error), patch.object(chrome_start, 'stale_profile_lock') as recovery:
                self.assertEqual(chrome_start.check_ready(self.config)['result'], expected)
            recovery.assert_not_called()

    def test_sending_startup_accepts_proven_stale_profile_without_waiting(self):
        with patch.object(chrome_start, 'read_processes', return_value=''), patch.object(chrome_start.os, 'kill', side_effect=ProcessLookupError), patch('selenium_controller.time.sleep') as sleep, contextlib.redirect_stderr(io.StringIO()) as output:
            ready = wait_for_profile_idle(self.config)
        self.assertTrue(ready['can_launch_new_instance'])
        self.assertIn('same saved profile', output.getvalue())
        sleep.assert_not_called()
