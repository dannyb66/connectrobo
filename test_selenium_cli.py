import contextlib
import io
import json
import unittest
from unittest.mock import Mock, patch
import connectrobo


class SeleniumCliTests(unittest.TestCase):
    def test_connect_once_without_extra_flags_defaults_to_selenium(self):
        c = Mock()
        with patch('selenium_controller.create_controller', return_value=c), patch('selenium_controller.wait_for_manual_login'), patch.object(connectrobo, 'run_connections', return_value={'result': 'CANDIDATES_EXHAUSTED'}) as run, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connectrobo.main(['--connect-once']), 0)
        self.assertTrue(run.call_args.args[2])
        self.assertEqual(run.call_args.kwargs['limit'], 50)

    def test_busy_timeout_has_actionable_result_without_sending(self):
        from selenium_controller import ProfileUnavailable
        output = io.StringIO()
        with patch('selenium_controller.create_controller', side_effect=ProfileUnavailable('Close ConnectRobo Chrome, then rerun.')), patch.object(connectrobo, 'run_connections') as run, contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connectrobo.main([]), 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report['result'], 'STOPPED_PROFILE_UNAVAILABLE')
        self.assertEqual(report['next'], 'Close ConnectRobo Chrome, then rerun.')
        self.assertEqual(report['attempted_or_reserved'], 0)
        run.assert_not_called()

    def test_run_exception_closes_owned_browser_and_does_not_claim_zero_attempts(self):
        c = Mock()
        output = io.StringIO()
        with patch('selenium_controller.create_controller', return_value=c), patch('selenium_controller.wait_for_manual_login'), patch.object(connectrobo, 'run_connections', side_effect=OSError('private receipt details')), contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connectrobo.main([]), 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report['result'], 'STOPPED_RUN_ERROR')
        self.assertIsNone(report['attempted_or_reserved'])
        self.assertNotIn('private receipt details', output.getvalue())
        c.driver.quit.assert_called_once()

    def test_explicit_adapter_login_failure_never_runs_engine_and_closes_owned_browser(self):
        c = Mock()
        with patch('selenium_controller.create_controller', return_value=c), patch('selenium_controller.wait_for_manual_login', side_effect=TimeoutError), patch.object(connectrobo, 'run_connections') as run, contextlib.redirect_stdout(io.StringIO()):
            status = connectrobo.main(['--connect-once', '--controller', 'selenium', '--run-id', 'synthetic'])
        self.assertEqual(status, 2)
        run.assert_not_called()
        c.driver.quit.assert_called_once()

    def test_explicit_adapter_passes_to_bounded_engine_and_closes_owned_browser(self):
        c = Mock()
        with patch('selenium_controller.create_controller', return_value=c), patch('selenium_controller.wait_for_manual_login'), patch.object(connectrobo, 'run_connections', return_value={'result':'CANDIDATES_EXHAUSTED'}) as run, contextlib.redirect_stdout(io.StringIO()):
            status = connectrobo.main(['--connect-once', '--controller', 'selenium', '--run-id', 'synthetic'])
        self.assertEqual(status, 0)
        self.assertIs(run.call_args.args[0], c)
        self.assertEqual(run.call_args.args[2], 'synthetic')
        c.driver.quit.assert_called_once()
