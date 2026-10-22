import contextlib
import io
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch

import chrome_start
import connectrobo
import selenium_controller
from test_profile_wait import report


class ProfileRestartTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name).resolve() / 'ConnectRobo/ChromeProfile'
        self.profile.mkdir(parents=True)
        self.state = {'profile_kind': 'connectrobo_persistent', 'user_data_dir': str(self.profile)}
        self.browser = '123 ' + str(chrome_start.CHROME) + ' --user-data-dir=' + str(self.profile)

    def test_only_exact_official_main_browser_is_selected(self):
        processes = '\n'.join([
            self.browser,
            '124 ' + str(chrome_start.CHROME) + ' --type=renderer --user-data-dir=' + str(self.profile),
            '125 ' + str(chrome_start.CHROME) + ' --user-data-dir=' + str(self.profile) + '-other',
            '126 python some-script --user-data-dir=' + str(self.profile),
            '127 /other/Google Chrome --user-data-dir=' + str(self.profile),
            '128 ' + str(chrome_start.CHROME) + ' --user-data-dir=' + str(self.profile.parent.parent / 'JobRobo/ChromeProfile'),
        ])
        with patch.object(chrome_start, 'read_processes', return_value=processes), patch.object(chrome_start.os, 'kill') as close:
            self.assertEqual(chrome_start.request_profile_close(self.state), 123)
        close.assert_called_once_with(123, signal.SIGTERM)

    def test_shared_profile_is_never_closed(self):
        self.state['profile_kind'] = 'configured_shared'
        with patch.object(chrome_start, 'read_processes') as processes, patch.object(chrome_start.os, 'kill') as close:
            with self.assertRaises(chrome_start.StartupError):
                chrome_start.request_profile_close(self.state)
        close.assert_not_called()
        processes.assert_not_called()

    def test_ambiguous_missing_or_changed_process_is_not_signaled(self):
        for observations in [('', ''), (self.browser + '\n' + self.browser.replace('123 ', '999 ', 1), ''),
                             (self.browser, ''), (self.browser, self.browser.replace('123 ', '999 ', 1))]:
            with self.subTest(observations=observations), patch.object(chrome_start, 'read_processes', side_effect=observations), patch.object(chrome_start.os, 'kill') as close:
                with self.assertRaises(chrome_start.StartupError):
                    chrome_start.request_profile_close(self.state)
            close.assert_not_called()

    def test_process_inspection_failure_never_signals(self):
        with patch.object(chrome_start, 'read_processes', side_effect=chrome_start.StartupError('Cannot inspect')), patch.object(chrome_start.os, 'kill') as close:
            with self.assertRaises(chrome_start.StartupError):
                chrome_start.request_profile_close(self.state)
        close.assert_not_called()

    def test_close_permission_failure_is_sanitized_and_process_exit_is_tolerated(self):
        with patch.object(chrome_start, 'read_processes', return_value=self.browser), patch.object(chrome_start.os, 'kill', side_effect=PermissionError('private detail')):
            with self.assertRaises(chrome_start.StartupError) as error:
                chrome_start.request_profile_close(self.state)
        self.assertNotIn('private detail', str(error.exception))
        with patch.object(chrome_start, 'read_processes', return_value=self.browser), patch.object(chrome_start.os, 'kill', side_effect=ProcessLookupError):
            self.assertEqual(chrome_start.request_profile_close(self.state), 123)

    def test_restart_signals_once_and_waits_for_locks_to_clear_without_deletion(self):
        marker = self.profile / 'saved-profile-marker'
        marker.write_text('preserve login profile')
        states = [report('running'), report('running'), report('locked_without_matching_process'), report('idle')]
        with patch.object(chrome_start, 'check_ready', side_effect=states), patch.object(chrome_start, 'request_profile_close', return_value=123) as close, patch('selenium_controller.time.sleep'), patch('selenium_controller.time.monotonic', return_value=0), contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(selenium_controller.wait_for_profile_idle('/fixture', 10, restart_browser=True)['can_launch_new_instance'])
        close.assert_called_once()
        self.assertEqual(marker.read_text(), 'preserve login profile')

    def test_restart_timeout_never_repeats_close_request(self):
        with patch.object(chrome_start, 'check_ready', return_value=report('running')), patch.object(chrome_start, 'request_profile_close', return_value=123) as close, patch('selenium_controller.time.sleep'), patch('selenium_controller.time.monotonic', side_effect=[0, 0, 2]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(selenium_controller.ProfileUnavailable):
                selenium_controller.wait_for_profile_idle('/fixture', 1, restart_browser=True)
        close.assert_called_once()

    def test_restart_idle_profile_does_not_close_anything(self):
        with patch.object(chrome_start, 'check_ready', return_value=report('idle')), patch.object(chrome_start, 'request_profile_close') as close:
            self.assertTrue(selenium_controller.wait_for_profile_idle('/fixture', 10, restart_browser=True)['can_launch_new_instance'])
        close.assert_not_called()

    def test_restart_lock_only_or_unknown_profile_does_not_close_anything(self):
        for state in ('locked_without_matching_process', 'unknown'):
            with self.subTest(state=state), patch.object(chrome_start, 'check_ready', return_value=report(state)), patch.object(chrome_start, 'request_profile_close') as close:
                with self.assertRaises(selenium_controller.ProfileUnavailable):
                    selenium_controller.wait_for_profile_idle('/fixture', 10, restart_browser=True)
            close.assert_not_called()

    def test_cli_passes_restart_option_to_selenium(self):
        controller = Mock()
        with patch.object(selenium_controller, 'create_controller', return_value=controller) as create, patch.object(selenium_controller, 'wait_for_manual_login'), patch.object(connectrobo, 'run_connections', return_value={'result': 'CANDIDATES_EXHAUSTED'}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connectrobo.main(['--restart-browser']), 0)
        self.assertTrue(create.call_args.kwargs['restart_browser'])
        controller.driver.quit.assert_called_once()

    def test_restart_is_rejected_for_read_only_modes_or_zero_wait(self):
        for args in [['--restart-browser', '--preflight'], ['--restart-browser', '--open-only'],
                     ['--restart-browser', '--profile-timeout', '0']]:
            with self.subTest(args=args), patch.object(selenium_controller, 'create_controller') as create, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                connectrobo.main(args)
            create.assert_not_called()
