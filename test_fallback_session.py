import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import chrome_start as launcher
from fallback_session import observe_login, prepare_private_session


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.primary = self.root / 'shared-profile'
        self.primary.mkdir()
        self.report = {'mode': 'read_only_no_send', 'profile_state': 'running',
                       'profile_idle': False, 'browser_open': True, 'can_launch_new_instance': False,
                       'configuration_ready': True, 'automation_ready': False, 'attachment_supported': False,
                       'fallback_proposed': True, 'user_data_dir': str(self.primary),
                       'browser_launched': False, 'login_verified': False, 'outreach_actions': 0,
                       'launch_arguments': ['primary-must-never-launch'], 'result': 'BROWSER_ALREADY_OPEN'}
        self.patches = [patch.object(launcher, 'ROOT', self.root),
                        patch.object(launcher.sys, 'platform', 'darwin'),
                        patch('fallback_session.tempfile.gettempdir', return_value=str(self.root))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def mkdir(self, **kwargs):
        path = self.root / 'connectrobo-session-new'
        path.mkdir()
        return str(path)

    def test_preflight_only_proposes_fallback_without_creating_session(self):
        with patch.object(launcher, 'check_ready', return_value=dict(self.report)), patch('fallback_session.tempfile.mkdtemp') as create, patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.start('/unused', False)
        self.assertTrue(result['fallback_proposed'])
        create.assert_not_called()
        spawn.assert_not_called()
        self.assertFalse((self.root / '.connectrobo-fallback.json').exists())

    def test_busy_shared_profile_launches_distinct_private_login_session(self):
        child = Mock(pid=456)
        child.poll.return_value = None
        with patch.object(launcher, 'check_ready', return_value=dict(self.report)), patch.object(launcher, 'read_processes', return_value=''), patch('fallback_session.tempfile.mkdtemp', side_effect=self.mkdir), patch.object(launcher.subprocess, 'Popen', return_value=child) as spawn, patch.object(launcher.time, 'sleep'):
            result = launcher.start('/unused', True)
        args = spawn.call_args.args[0]
        self.assertIn('--incognito', args)
        self.assertNotIn('--user-data-dir=' + str(self.primary), args)
        self.assertEqual(args[-1], 'https://www.linkedin.com/login/')
        self.assertEqual(result['result'], 'PRIVATE_BROWSER_LOGIN_HANDOFF')
        self.assertFalse(result['login_verified'])
        self.assertFalse(result['automation_ready'])
        self.assertEqual(result['outreach_actions'], 0)
        self.assertFalse(any('remote-debugging' in arg for arg in args))

    def existing(self):
        profile = self.root / 'connectrobo-session-existing'
        profile.mkdir()
        (self.root / '.connectrobo-fallback.json').write_text(json.dumps({'user_data_dir': str(profile)}))
        return profile

    def test_busy_fallback_hands_off_without_spawning_again(self):
        profile = self.existing()
        text = '789 chrome --user-data-dir=' + str(profile)
        with patch.object(launcher, 'check_ready', return_value=dict(self.report)), patch.object(launcher, 'read_processes', return_value=text), patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.start('/unused', True)
        self.assertEqual(result['result'], 'PRIVATE_BROWSER_ALREADY_OPEN')
        self.assertFalse(result['browser_launched'])
        self.assertFalse(result['login_verified'])
        spawn.assert_not_called()

    def test_fallback_lock_without_visible_process_is_preserved(self):
        profile = self.existing()
        lock = profile / 'SingletonLock'
        lock.symlink_to('/unobservable-or-stale')
        result = prepare_private_session(self.report, self.root, launcher.CHROME, lambda: '', launcher.active_profile_pids)
        self.assertEqual(result['result'], 'PRIVATE_PROFILE_LOCKED')
        self.assertTrue(lock.is_symlink())
        self.assertFalse(result['can_launch_new_instance'])

    def test_unknown_process_state_does_not_create_fallback(self):
        with patch.object(launcher, 'check_ready', return_value=dict(self.report)), patch.object(launcher, 'read_processes', side_effect=launcher.StartupError('No process access')), patch('fallback_session.tempfile.mkdtemp') as create, patch.object(launcher.subprocess, 'Popen') as spawn:
            with self.assertRaises(launcher.StartupError):
                launcher.start('/unused', True)
        create.assert_not_called()
        spawn.assert_not_called()

    def test_arbitrary_or_primary_profile_in_fallback_record_is_rejected(self):
        (self.root / '.connectrobo-fallback.json').write_text(json.dumps({'user_data_dir': str(self.primary)}))
        with self.assertRaises(ValueError):
            prepare_private_session(self.report, self.root, launcher.CHROME, lambda: '', launcher.active_profile_pids)

    def test_login_needed_verified_and_timeout_are_distinct_observations(self):
        cases = [({'url': 'https://www.linkedin.com/login/', 'login_form_present': True}, 'login_needed', False),
                 ({'url': 'https://www.linkedin.com/feed/', 'login_form_present': False, 'account_menu_present': True, 'feed_navigation_present': True}, 'verified', True),
                 ({'url': 'https://evil.example/feed/', 'account_menu_present': True, 'feed_navigation_present': True}, 'observation_required', False),
                 ({'url': 'https://www.linkedin.com/feed/', 'user_reported_login': True}, 'observation_required', False)]
        for page, state, verified in cases:
            result = observe_login(lambda **kwargs: page)
            self.assertEqual(result['login_state'], state)
            self.assertEqual(result['login_verified'], verified)
        observer = Mock(side_effect=TimeoutError)
        self.assertEqual(observe_login(observer, 2)['login_state'], 'observation_timeout')
        observer.assert_called_once_with(timeout_seconds=2)
        self.assertFalse(observe_login()['login_verified'])

    def test_supported_observer_can_resume_only_readonly_readiness(self):
        self.existing()
        text = '789 chrome --user-data-dir=' + str(self.root / 'connectrobo-session-existing')
        page = {'url': 'https://www.linkedin.com/feed/', 'login_form_present': False,
                'account_menu_present': True, 'feed_navigation_present': True}
        with patch.object(launcher, 'check_ready', return_value=dict(self.report)), patch.object(launcher, 'read_processes', return_value=text), patch.object(launcher.subprocess, 'Popen') as spawn:
            result = launcher.start('/unused', True, login_observer=lambda **kwargs: page)
        self.assertTrue(result['read_only_readiness_resumed'])
        self.assertTrue(result['login_verified'])
        self.assertEqual(result['outreach_actions'], 0)
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
