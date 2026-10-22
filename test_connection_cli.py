import contextlib
import io
import json
import unittest
from unittest.mock import patch
import connectrobo


class ConnectionCliTests(unittest.TestCase):
    def test_adapter_startup_failure_does_not_run_engine_or_claim_success(self):
        output = io.StringIO()
        with patch.object(connectrobo.chrome_start, 'main') as launch, patch('selenium_controller.create_controller', side_effect=RuntimeError('private browser details')), patch.object(connectrobo, 'run_connections') as run, contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connectrobo.main(['--connect-once', '--run-id', 'offline-test']), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result['result'], 'STOPPED_ADAPTER_STARTUP_OR_LOGIN')
        self.assertEqual(result['confirmed'], 0)
        self.assertEqual(result['attempted_or_reserved'], 0)
        launch.assert_not_called()
        run.assert_not_called()
        self.assertNotIn('private browser details', output.getvalue())

    def test_read_only_modes_reject_send_options_before_browser_access(self):
        for args in [['--open-only', '--connect-once'], ['--preflight', '--controller', 'selenium'],
                     ['--open-only', '--run-id', 'fixture'], ['--profile-timeout', '-1'],
                     ['--profile-timeout', '601'], ['--run-id', ' ']]:
            with self.subTest(args=args), patch('selenium_controller.create_controller') as create, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                connectrobo.main(args)
            create.assert_not_called()

    def test_preflight_cannot_be_combined_with_connection_run(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            connectrobo.main(['--preflight', '--connect-once', '--run-id', 'offline-test'])


if __name__ == '__main__':
    unittest.main()
