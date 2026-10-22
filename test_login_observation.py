import contextlib
import io
import unittest
from unittest.mock import Mock, patch
from selenium_controller import wait_for_manual_login, ControlError


class TimeoutException(Exception):
    pass


class LoginObservationTests(unittest.TestCase):
    def test_feed_without_navigation_is_not_a_verified_login(self):
        controller = Mock(verification_diagnostic={})
        controller.current_url.return_value = 'https://www.linkedin.com/feed/'
        controller.safety_state.return_value = 'ok'
        controller.navigation_ready.return_value = False
        with contextlib.redirect_stderr(io.StringIO()), patch('selenium_controller.time.sleep'), patch('selenium_controller.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaises(ControlError):
                wait_for_manual_login(controller, 1)
        controller.observe_verification.assert_not_called()

    def test_login_waits_for_navigation_to_render(self):
        controller = Mock(verification_diagnostic={})
        controller.current_url.return_value = 'https://www.linkedin.com/feed/'
        controller.safety_state.return_value = 'ok'
        controller.navigation_ready.side_effect = [False, True]
        with contextlib.redirect_stderr(io.StringIO()), patch('selenium_controller.time.sleep'):
            wait_for_manual_login(controller, 1)
        self.assertEqual(controller.navigation_ready.call_count, 2)
        controller.observe_verification.assert_called_once_with('login_wait_passed')

    def test_transient_read_timeout_retries_observation_only(self):
        c = Mock(verification_diagnostic={})
        c.current_url.return_value = 'https://www.linkedin.com/feed/'
        c.safety_state.side_effect = [TimeoutException(), 'ok']
        with contextlib.redirect_stderr(io.StringIO()), patch('selenium_controller.time.sleep'):
            wait_for_manual_login(c, 1)
        self.assertEqual(c.safety_state.call_count, 2)
        c.observe_verification.assert_called_once_with('login_wait_passed')
        c.driver.get.assert_not_called()
        c.driver.find_element.assert_not_called()

    def test_read_timeout_never_extends_original_deadline(self):
        c = Mock(verification_diagnostic={})
        c.current_url.return_value = 'https://www.linkedin.com/feed/'
        c.safety_state.side_effect = TimeoutException()
        with contextlib.redirect_stderr(io.StringIO()), patch('selenium_controller.time.sleep'), patch('selenium_controller.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaises(ControlError):
                wait_for_manual_login(c, 1)
        self.assertEqual(c.verification_diagnostic['stage'], 'login_wait_timeout')
        self.assertEqual(c.verification_diagnostic['login_read_timeout_count'], 1)
        c.observe_verification.assert_not_called()

    def test_none_route_is_pending_instead_of_type_error(self):
        from selenium_controller import SeleniumController, ObservationPending
        c = SeleniumController(Mock(current_url=None))
        with self.assertRaises(ObservationPending):
            c.current_url()
        self.assertEqual(c.verification_diagnostic['route_value_type'], 'NoneType')

    def test_nontext_route_never_passes_authentication(self):
        from selenium_controller import SeleniumController, ObservationPending
        c = SeleniumController(Mock(current_url=b''))
        with self.assertRaises(ObservationPending):
            c.safety_state()

    def test_missing_route_waits_within_deadline_without_browser_action(self):
        from selenium_controller import SeleniumController
        driver = Mock(current_url=None)
        c = SeleniumController(driver)
        with contextlib.redirect_stderr(io.StringIO()), patch('selenium_controller.time.sleep'), patch('selenium_controller.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaises(ControlError):
                wait_for_manual_login(c, 1)
        self.assertEqual(c.verification_diagnostic['route_value_type'], 'NoneType')
        self.assertEqual(c.verification_diagnostic['stage'], 'login_wait_timeout')
        driver.get.assert_not_called()

    def test_checkpoint_never_retries(self):
        c = Mock(verification_diagnostic={})
        c.current_url.return_value = 'https://www.linkedin.com/checkpoint/'
        with self.assertRaises(ControlError):
            wait_for_manual_login(c, 1)
        c.observe_verification.assert_called_once_with('login_checkpoint')
        c.safety_state.assert_not_called()
