import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import connectrobo
from run_config import load_run_config, validate_run_config


class RunConfigTests(unittest.TestCase):
    def test_settings_are_reloaded_and_forwarded_independently_on_each_run(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'custom.json'
            for limits in ((50, 20, 200), (3, 7, 11)):
                settings = dict(keywords='founder iit bay area', network=['S'], login_timeout_seconds=180, profile_timeout_seconds=180, **dict(zip(('max_attempts', 'max_pages', 'max_candidates'), limits)))
                path.write_text(json.dumps(settings))
                with patch.object(connectrobo, 'run_connections', return_value={'result': 'CANDIDATES_EXHAUSTED'}) as run, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(connectrobo.main(['--run-config', str(path)], controller=object()), 0)
                self.assertEqual(run.call_args.kwargs, dict(limit=limits[0], max_pages=limits[1], max_candidates=limits[2], keywords=settings['keywords'], network=settings['network']))

    def test_missing_or_invalid_settings_fail_before_browser_or_engine(self):
        valid = dict(max_attempts=50, max_pages=20, max_candidates=200, keywords='founder iit bay area', network=['S'], login_timeout_seconds=180, profile_timeout_seconds=180)
        invalid = ['{', 'null', '[]', '{}', json.dumps(dict(valid, max_pagess=3))]
        for key in ('max_attempts', 'max_pages', 'max_candidates'):
            for value in (0, -1, True, 2.5, '20', None):
                invalid.append(json.dumps(dict(valid, **{key: value})))
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'settings.json'
            for contents in [None] + invalid:
                if contents is not None:
                    path.write_text(contents)
                with self.subTest(contents=contents), patch('selenium_controller.create_controller') as create, patch.object(connectrobo, 'run_connections') as run, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    connectrobo.main(['--run-config', str(path)])
                create.assert_not_called()
                run.assert_not_called()

    def test_positive_limits_are_independent(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'settings.json'
            settings = dict(max_attempts=50, max_pages=1, max_candidates=2, keywords='custom search', network=['S'], login_timeout_seconds=25, profile_timeout_seconds=0)
            path.write_text(json.dumps(settings))
            self.assertEqual(load_run_config(path), settings)

    def test_invalid_search_and_wait_settings_are_rejected(self):
        valid = dict(max_attempts=50, max_pages=20, max_candidates=200, keywords='custom', network=['S'],
                     login_timeout_seconds=180, profile_timeout_seconds=180)
        for key, values in [('keywords', ['', ' ', None, 12]),
                            ('network', [[], 'S', ['S', 'S'], ['unknown'], [True], [None]]),
                            ('login_timeout_seconds', [0, -1, 601, True, '180', 1.5]),
                            ('profile_timeout_seconds', [-1, 601, True, '180', 1.5])]:
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_run_config(dict(valid, **{key: value}))

    def test_configured_timeouts_and_explicit_overrides_reach_controller(self):
        settings = dict(max_attempts=50, max_pages=20, max_candidates=200, keywords='custom search', network=['S'],
                        login_timeout_seconds=12, profile_timeout_seconds=34)
        for flags, expected in [([], (12, 34)), (['--login-timeout', '9', '--profile-timeout', '10'], (9, 10))]:
            with self.subTest(flags=flags), patch.object(connectrobo, 'load_run_config', return_value=settings), patch('selenium_controller.create_controller') as create, patch('selenium_controller.wait_for_manual_login') as login, patch.object(connectrobo, 'run_connections', return_value={'result':'CANDIDATES_EXHAUSTED'}), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                connectrobo.main(flags)
            self.assertEqual(create.call_args.kwargs['profile_timeout'], expected[1])
            login.assert_called_once_with(create.return_value, expected[0])

    def test_restart_rejects_zero_configured_wait_before_browser_access(self):
        settings = dict(max_attempts=50, max_pages=20, max_candidates=200, keywords='custom', network=['S'],
                        login_timeout_seconds=180, profile_timeout_seconds=0)
        with patch.object(connectrobo, 'load_run_config', return_value=settings), patch('selenium_controller.create_controller') as create, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            connectrobo.main(['--restart-browser'])
        create.assert_not_called()

    def test_read_only_startup_does_not_require_run_settings(self):
        with patch.object(connectrobo.chrome_start, 'main', return_value=0), patch.object(connectrobo, 'load_run_config') as load:
            self.assertEqual(connectrobo.main(['--preflight', '--run-config', '/nonexistent.json']), 0)
        load.assert_not_called()
