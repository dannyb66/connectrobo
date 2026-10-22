import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import connectrobo
from test_connection_run import Controller, candidate


class EntrypointTests(unittest.TestCase):
    def test_open_only_uses_guarded_launcher_without_controller_or_sending(self):
        with patch.object(connectrobo.chrome_start, 'main', return_value=0) as launch, patch('selenium_controller.create_controller') as create, patch.object(connectrobo, 'run_connections') as run:
            self.assertEqual(connectrobo.main(['--open-only']), 0)
        args = launch.call_args.args[0]
        self.assertEqual(args[-1], '--open')
        self.assertTrue(args[1].endswith('/chrome_session.json'))
        create.assert_not_called()
        run.assert_not_called()

    def test_default_starts_selenium_with_configured_bounds(self):
        controller = Mock()
        with patch('selenium_controller.create_controller', return_value=controller) as create, patch('selenium_controller.wait_for_manual_login') as login, patch.object(connectrobo, 'run_connections', return_value={'result': 'ATTEMPT_CAP_REACHED'}) as run, patch.object(connectrobo.chrome_start, 'main') as launch, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(connectrobo.main([]), 0)
        self.assertEqual(create.call_args.kwargs['profile_timeout'], 180)
        login.assert_called_once_with(controller, 180)
        self.assertIs(run.call_args.args[0], controller)
        self.assertTrue(run.call_args.args[2])
        self.assertEqual(run.call_args.kwargs['limit'], 50)
        controller.driver.quit.assert_called_once()
        launch.assert_not_called()

    def test_two_default_runs_each_get_twenty_attempts_with_distinct_receipts(self):
        with tempfile.TemporaryDirectory() as root, patch.object(connectrobo, '__file__', str(Path(root) / 'connectrobo.py')), patch('selenium_controller.create_controller') as create, contextlib.redirect_stderr(io.StringIO()):
            (Path(root) / 'run_config.json').write_text(json.dumps(dict(keywords='founder iit bay area', network=['S'], max_attempts=20, max_pages=5, max_candidates=100, login_timeout_seconds=180, profile_timeout_seconds=180)))
            reports = []
            for start in (0, 100):
                controller = Controller([candidate(i) for i in range(start, start + 35)])
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(connectrobo.main([], controller=controller), 0)
                reports.append(json.loads(output.getvalue()))
                self.assertEqual(len(controller.calls), 20)
            rows = [json.loads(line) for line in (Path(root) / 'logs/invitation-receipts.jsonl').read_text().splitlines()]
        self.assertNotEqual(reports[0]['run_id'], reports[1]['run_id'])
        self.assertEqual([r['confirmed'] for r in reports], [20, 20])
        self.assertEqual(sum(row['status'] == 'attempt_reserved' for row in rows), 40)
        create.assert_not_called()

    def test_preflight_preserves_custom_config_and_launcher_errors(self):
        with patch.object(connectrobo.chrome_start, 'main', return_value=2) as launch, patch('selenium_controller.create_controller') as create, patch.object(connectrobo, 'run_connections') as run:
            self.assertEqual(connectrobo.main(['--config', '/tmp/test-config.json', '--preflight']), 2)
        self.assertEqual(launch.call_args.args[0], ['--config', '/tmp/test-config.json'])
        create.assert_not_called()
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
