import unittest
from unittest.mock import patch
import connectrobo


class EntrypointTests(unittest.TestCase):
    def test_normal_run_reopens_saved_profile_with_guarded_launcher(self):
        with patch.object(connectrobo.chrome_start, 'main', return_value=0) as launch:
            self.assertEqual(connectrobo.main([]), 0)
        args = launch.call_args.args[0]
        self.assertEqual(args[-1], '--open')
        self.assertTrue(args[1].endswith('/chrome_session.json'))

    def test_preflight_preserves_custom_config_and_launcher_errors(self):
        with patch.object(connectrobo.chrome_start, 'main', return_value=2) as launch:
            self.assertEqual(connectrobo.main(['--config', '/tmp/test-config.json', '--preflight']), 2)
        self.assertEqual(launch.call_args.args[0], ['--config', '/tmp/test-config.json'])


if __name__ == '__main__':
    unittest.main()
