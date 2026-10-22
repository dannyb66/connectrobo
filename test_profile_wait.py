import contextlib
import io
import unittest
from unittest.mock import patch

from selenium_controller import ProfileUnavailable, wait_for_profile_idle


def report(state):
    return {'profile_state': state, 'can_launch_new_instance': state == 'idle'}


class ProfileWaitTests(unittest.TestCase):
    def test_idle_profile_returns_without_sleeping(self):
        with patch('chrome_start.check_ready', return_value=report('idle')), patch('selenium_controller.time.sleep') as sleep:
            self.assertTrue(wait_for_profile_idle('/fixture')['can_launch_new_instance'])
        sleep.assert_not_called()

    def test_busy_profile_prompts_then_continues_automatically_when_closed(self):
        output = io.StringIO()
        with patch('chrome_start.check_ready', side_effect=[report('running'), report('running'), report('idle')]) as check, patch('selenium_controller.time.sleep'), patch('selenium_controller.time.monotonic', return_value=0), contextlib.redirect_stderr(output):
            self.assertTrue(wait_for_profile_idle('/fixture', 10)['can_launch_new_instance'])
        self.assertEqual(check.call_count, 3)
        self.assertEqual(output.getvalue().count('Close that Chrome instance'), 1)

    def test_busy_profile_wait_is_bounded(self):
        with patch('chrome_start.check_ready', return_value=report('running')), patch('selenium_controller.time.sleep') as sleep, patch('selenium_controller.time.monotonic', side_effect=[0, 0, 2]), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ProfileUnavailable) as error:
                wait_for_profile_idle('/fixture', 1)
        self.assertIn('Timed out', error.exception.next_step)
        sleep.assert_called_once_with(1)

    def test_unknown_or_lock_only_state_stops_without_waiting(self):
        for state in ('unknown', 'locked_without_matching_process'):
            with self.subTest(state=state), patch('chrome_start.check_ready', return_value=report(state)), patch('selenium_controller.time.sleep') as sleep:
                with self.assertRaises(ProfileUnavailable):
                    wait_for_profile_idle('/fixture')
            sleep.assert_not_called()
