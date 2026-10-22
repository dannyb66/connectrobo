import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from selenium_controller import SeleniumController, ControlError, ObservationTimeout, wait_for_manual_login

URL = 'https://www.linkedin.com/in/fixture-person/'


class AdapterTests(unittest.TestCase):
    def email_controller(self):
        c = self.controller()
        c._state = Mock(return_value='connect')
        c._profile_heading = Mock(return_value=Mock(text='Fixture Person'))
        dialog = Mock(text='Add a note to your invitation? To verify this member knows you, please enter their email to connect. You can also include a personal note.')
        field = Mock(tag_name='input')
        field.get_attribute.side_effect = lambda key: {'type': 'email', 'value': ''}.get(key)
        send = Mock(text='Send without a note')
        send.is_enabled.return_value = False
        dismiss = Mock()
        active = [dialog]
        dismiss.click.side_effect = active.clear
        c._invitation_dialogs = Mock(side_effect=lambda: list(active))
        def visible(root, selector):
            if root is dialog:
                if selector.startswith('textarea, input'):
                    return [field]
                if selector.startswith('button[aria-label="Dismiss"]'):
                    return [dismiss]
                if selector.startswith('button, a'):
                    return [send]
            return []
        c._visible = Mock(side_effect=visible)
        connect = Mock()
        c._button = Mock(return_value=connect)
        return c, field, connect, send, dismiss

    def test_email_required_dialog_is_dismissed_and_skipped_without_typing_or_send(self):
        c, field, connect, send, dismiss = self.email_controller()
        result = c.submit_no_note(URL, 'Fixture Person')
        self.assertEqual(result, dict(status='skipped_email_required', profile_url=URL,
                                     evidence='email_required_before_send'))
        self.assertEqual(c.verification_diagnostic['stage'], 'invitation_email_skipped')
        connect.click.assert_called_once()
        dismiss.click.assert_called_once()
        field.send_keys.assert_not_called()
        send.click.assert_not_called()
        c._button.assert_called_once_with(c._top.return_value, 'Connect')

    def test_email_skip_requires_same_profile_and_successful_dismissal(self):
        for case in ('recipient_changed', 'send_enabled', 'dismiss_failed', 'state_changed'):
            with self.subTest(case=case):
                c, field, connect, send, dismiss = self.email_controller()
                if case == 'recipient_changed':
                    c._profile_heading.return_value.text = 'Another Person'
                elif case == 'send_enabled':
                    send.is_enabled.return_value = True
                elif case == 'dismiss_failed':
                    dismiss.click.side_effect = None
                else:
                    c._state.side_effect = ['connect', 'connect', 'pending']
                with self.assertRaises(ControlError):
                    c.submit_no_note(URL, 'Fixture Person')
                field.send_keys.assert_not_called()
                send.click.assert_not_called()

    def late_alert_controller(self, messages, timing='nested'):
        """Replay the observed toast arriving during the nested _top safety read."""
        c = self.controller()
        alerts = []
        c._safe = SeleniumController._safe.__get__(c)
        c._ack_snapshot = SeleniumController._ack_snapshot.__get__(c)
        c._invitation_ack = SeleniumController._invitation_ack.__get__(c)
        c._profile_heading = Mock(return_value=Mock(text='Fixture Person'))
        c._state = Mock(return_value='connect')
        c._visible = Mock(side_effect=lambda root, selector: list(alerts) if '[role="alert"]' in selector else [])
        c._invitation_dialogs = Mock(return_value=[Mock(text='Invite Fixture Person')])
        calls = 0
        def show_alerts():
            alerts.extend(Mock(id=str(index), text=text) for index, text in enumerate(messages))
        def top():
            nonlocal calls
            calls += 1
            if timing == 'nested' and calls == 3:
                show_alerts()
            c._safe()
            return Mock()
        c._top = Mock(side_effect=top)
        connect, send = Mock(), Mock()
        if timing == 'direct':
            send.click.side_effect = show_alerts
        c._button = Mock(side_effect=[connect, send])
        return c, connect, send

    def test_success_toast_during_nested_top_safety_read_confirms_without_resending(self):
        c, connect, send = self.late_alert_controller(['Invitation sent to Fixture.'])
        result = c.submit_no_note(URL, 'Fixture Person')
        self.assertEqual(result['evidence'], 'invitation_sent_after_submit')
        self.assertEqual(c.safety_state(), 'ok')
        connect.click.assert_called_once()
        send.click.assert_called_once()

    def test_compound_given_name_in_success_toast_confirms_the_full_profile(self):
        for timing in ('nested', 'direct'):
            with self.subTest(timing=timing):
                c, connect, send = self.late_alert_controller(
                    ['Invitation sent to Shubhankar Mihir.'], timing)
                c._profile_name.return_value = 'Shubhankar Mihir Seth'
                c._profile_heading.return_value.text = 'Shubhankar Mihir Seth'
                c._invitation_dialogs.return_value = [Mock(text='Invite Shubhankar Mihir Seth')]
                result = c.submit_no_note(URL, 'Shubhankar Mihir Seth')
                self.assertEqual(result['evidence'], 'invitation_sent_after_submit')
                self.assertEqual(c.safety_state(), 'ok')
                connect.click.assert_called_once()
                send.click.assert_called_once()

    def test_compound_given_name_requires_fresh_whole_leading_words_and_exact_identity(self):
        c = SeleniumController(Mock(current_url=URL), timeout=0)
        c._profile_heading = Mock(return_value=Mock(text='Shubhankar Mihir Seth'))
        for name, accepted in [('Shubhankar', True), ('Shubhankar Mihir', True),
                               ('Shubhankar Mihir Seth', True), ('Mihir Seth', False),
                               ('Shubhankar Mi', False), ('Shubhankar Mihir Other', False),
                               ('Seth', False)]:
            with self.subTest(name=name):
                c._ack_snapshot = Mock(return_value={('fresh', 'Invitation sent to ' + name + '.')})
                self.assertEqual(c._invitation_ack(URL, 'Shubhankar Mihir Seth', set()), accepted)
        c._ack_snapshot.return_value = {('existing', 'Invitation sent to Shubhankar Mihir.')}
        self.assertFalse(c._invitation_ack(URL, 'Shubhankar Mihir Seth', c._ack_snapshot()))
        c._profile_heading.return_value.text = 'Shubhankar Mihir Other'
        with self.assertRaises(ControlError):
            c._invitation_ack(URL, 'Shubhankar Mihir Seth', set())

    def test_compound_given_name_does_not_suppress_other_warnings(self):
        c, connect, send = self.late_alert_controller(
            ['Invitation sent to Shubhankar Mihir.', 'Invitation limit reached'], 'direct')
        c._profile_name.return_value = 'Shubhankar Mihir Seth'
        c._profile_heading.return_value.text = 'Shubhankar Mihir Seth'
        c._invitation_dialogs.return_value = [Mock(text='Invite Shubhankar Mihir Seth')]
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Shubhankar Mihir Seth')
        connect.click.assert_called_once()
        send.click.assert_called_once()

    def test_success_toast_with_another_warning_still_stops(self):
        for timing in ('nested', 'direct'):
            with self.subTest(timing=timing):
                c, connect, send = self.late_alert_controller(
                    ['Invitation sent to Fixture.', 'Invitation limit reached'], timing)
                with self.assertRaises(ControlError):
                    c.submit_no_note(URL, 'Fixture Person')
                connect.click.assert_called_once()
                send.click.assert_called_once()

    def test_late_warning_for_another_recipient_still_stops(self):
        c, connect, send = self.late_alert_controller(['Invitation sent to Someone Else.'])
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Fixture Person')
        connect.click.assert_called_once()
        send.click.assert_called_once()

    def test_preexisting_identical_success_toast_cannot_explain_late_warning(self):
        c, connect, send = self.late_alert_controller(['Invitation sent to Fixture.'])
        snapshot = c._ack_snapshot
        first = True
        def with_existing_toast():
            nonlocal first
            if first:
                first = False
                return {('0', 'Invitation sent to Fixture.')}
            return snapshot()
        c._ack_snapshot = with_existing_toast
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Fixture Person')
        self.assertIsNone(c._confirmed_invitation_ack)
        connect.click.assert_called_once()
        send.click.assert_called_once()

    def test_success_toast_does_not_override_checkpoint_or_recipient_change(self):
        for changed_url, changed_name in [('https://www.linkedin.com/checkpoint/challenge/', 'Fixture Person'),
                                          ('https://www.linkedin.com/in/other-person/', 'Fixture Person'),
                                          (URL, 'Another Person')]:
            with self.subTest(changed_url=changed_url, changed_name=changed_name):
                c, connect, send = self.late_alert_controller(['Invitation sent to Fixture.'], timing='direct')
                original_click = send.click.side_effect
                def change_after_send():
                    original_click()
                    c.driver.current_url = changed_url
                    c._profile_heading.return_value.text = changed_name
                send.click.side_effect = change_after_send
                with self.assertRaises(ControlError):
                    c.submit_no_note(URL, 'Fixture Person')
                connect.click.assert_called_once()
                send.click.assert_called_once()

    def test_confirmation_failure_saves_private_screenshot_and_fresh_warning_diagnostics(self):
        from connection_run import verification_diagnostic
        c, connect, send = self.late_alert_controller(['Invitation limit reached'])
        with tempfile.TemporaryDirectory() as root:
            c.evidence_dir = Path(root)
            def save(path):
                Path(path).write_bytes(b'synthetic screenshot')
                return True
            c.driver.save_screenshot.side_effect = save
            with self.assertRaises(ControlError):
                c.submit_no_note(URL, 'Fixture Person')
            diagnostic = verification_diagnostic(c)
            self.assertEqual(diagnostic['safety_state'], 'warning')
            self.assertEqual(diagnostic['alert_count'], 1)
            self.assertEqual(diagnostic['authentication_control_count'], 0)
            self.assertTrue(diagnostic['failure_screenshot_saved'])
            image = Path(root) / diagnostic['failure_screenshot']
            self.assertEqual(image.stat().st_mode & 0o777, 0o600)
            self.assertEqual(image.read_bytes(), b'synthetic screenshot')
            self.assertEqual(list(Path(root).iterdir()), [image])
            self.assertNotIn('Invitation limit reached', str(diagnostic))

    def test_screenshot_failure_preserves_original_control_error(self):
        c, connect, send = self.late_alert_controller(['Invitation limit reached'])
        with tempfile.TemporaryDirectory() as root:
            c.evidence_dir = Path(root)
            c.driver.save_screenshot.side_effect = OSError('synthetic screenshot failure')
            with self.assertRaisesRegex(ControlError, 'Authentication, warning or scope'):
                c.submit_no_note(URL, 'Fixture Person')
            self.assertFalse(c.verification_diagnostic['failure_screenshot_saved'])
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_search_respects_configured_twenty_page_limit_and_early_exhaustion(self):
        from test_connection_run import SEARCH_URL
        for available in (2, 25):
            with self.subTest(available=available):
                driver = Mock(current_url=SEARCH_URL)
                driver.get.side_effect = lambda url: setattr(driver, 'current_url', url)
                c = SeleniumController(driver, timeout=0)
                c._safe = Mock()
                c._visible = Mock(return_value=[Mock()])
                c._search_snapshot = Mock(side_effect=[([], page < available) for page in range(1, available + 1)])
                self.assertEqual(list(c.iter_candidates(SEARCH_URL, max_pages=20)), [])
                self.assertEqual(driver.get.call_count, min(20, available))
                self.assertEqual(driver.get.call_args.args[0], SEARCH_URL + '&page=' + str(min(20, available)))

    def test_missing_toast_uses_read_only_sent_receipt_without_resending(self):
        c = self.controller()
        c._state = Mock(return_value='connect')
        c._wait = Mock(side_effect=[Mock(text='Invite Fixture Person'), ObservationTimeout('receipt missed')])
        c._visible = Mock(return_value=[])
        c._sent_invitation_receipt = Mock(return_value=True)
        connect, send = Mock(), Mock()
        c._button = Mock(side_effect=[connect, send])
        result = c.submit_no_note(URL, 'Fixture Person')
        self.assertEqual(result['evidence'], 'sent_invitation_list_after_submit')
        connect.click.assert_called_once()
        send.click.assert_called_once()
        c._sent_invitation_receipt.assert_called_once_with(URL, 'Fixture Person')

    def test_success_toast_arriving_between_receipt_and_safety_reads_is_confirmed(self):
        c = self.controller()
        c._state = Mock(side_effect=['connect', 'connect'])
        c._visible = Mock(side_effect=[[Mock(text='Invite Fixture Person')], []])
        c._safe = Mock(side_effect=[None, ControlError('alert appeared'), None])
        c.safety_state = Mock(return_value='warning')
        c._invitation_ack = Mock(side_effect=[False, True])
        connect, send = Mock(), Mock()
        c._button = Mock(side_effect=[connect, send])
        self.assertEqual(c.submit_no_note(URL, 'Fixture Person')['evidence'], 'invitation_sent_after_submit')
        send.click.assert_called_once()

    def test_send_success_receipt_is_accepted_when_pending_control_is_not_shown(self):
        c = self.controller()
        c._state = Mock(side_effect=['connect', 'connect'])
        c._visible = Mock(side_effect=[[Mock(text='Invite Fixture Person')], []])
        c._invitation_ack = Mock(return_value=True)
        connect, send = Mock(), Mock()
        c._button = Mock(side_effect=[connect, send])
        result = c.submit_no_note(URL, 'Fixture Person')
        self.assertEqual(result['evidence'], 'invitation_sent_after_submit')
        self.assertEqual(c.verification_diagnostic['stage'], 'invitation_confirmed')
        send.click.assert_called_once()

    def test_native_open_dialog_without_explicit_role_sends_once(self):
        c = self.controller()
        c._state = Mock(side_effect=['connect', 'connect', 'pending'])
        dialog = Mock(text='Personalize your invitation to Fixture Person by adding a note.')
        c._visible = Mock(side_effect=lambda root, selector: [dialog] if 'dialog[open]' in selector else [])
        connect, send = Mock(), Mock()
        c._button = Mock(side_effect=[connect, send])
        self.assertEqual(c.submit_no_note(URL, 'Fixture Person')['status'], 'confirmed')
        connect.click.assert_called_once()
        send.click.assert_called_once()

    def test_recipient_bound_invitation_route_can_send_once_and_confirm_pending(self):
        c = self.controller()
        invite = 'https://www.linkedin.com/preload/custom-invite/?vanityName=fixture-person'
        connect = Mock(tag_name='a')
        connect.get_attribute.return_value = invite
        connect.click.side_effect = lambda: setattr(c.driver, 'current_url', invite)
        send = Mock()
        send.click.side_effect = lambda: setattr(c.driver, 'current_url', URL)
        c._state = Mock(side_effect=['connect', 'pending'])
        dialog = Mock(text='Invite Fixture Person to connect')
        c._visible = Mock(side_effect=[[dialog], []])
        c._button = Mock(side_effect=[connect, send])
        result = c.submit_no_note(URL, 'Fixture Person')
        self.assertEqual(result['status'], 'confirmed')
        connect.click.assert_called_once()
        send.click.assert_called_once()

    def test_invitation_route_for_another_recipient_is_rejected_before_click(self):
        c = self.controller()
        c._state = Mock(return_value='connect')
        connect = Mock(tag_name='a')
        connect.get_attribute.return_value = 'https://www.linkedin.com/preload/custom-invite/?vanityName=someone-else'
        c._button = Mock(return_value=connect)
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Fixture Person')
        connect.click.assert_not_called()

    def test_foreign_or_ambiguous_invitation_routes_are_rejected(self):
        c = self.controller()
        for route in ('https://example.org/preload/custom-invite/?vanityName=fixture-person',
                      'https://www.linkedin.com/preload/custom-invite/?vanityName=fixture-person&vanityName=other',
                      'https://www.linkedin.com/preload/custom-invite/?vanityName=other',
                      'https://www.linkedin.com/in/other/'):
            with self.subTest(route=route), self.assertRaises(ControlError):
                c._invitation_context(URL, route)

    def controller(self):
        driver = Mock(current_url=URL)
        c = SeleniumController(driver, timeout=0)
        c._safe = Mock()
        c.observe_verification = Mock()
        c._top = Mock(return_value=Mock())
        c._one = Mock(return_value=Mock(text='Fixture Person'))
        c._profile_name = Mock(return_value='Fixture Person')
        c._ack_snapshot = Mock(return_value=set())
        c._invitation_ack = Mock(return_value=False)
        return c

    def test_direct_pending_requires_one_connect_click(self):
        c = self.controller()
        c._state = Mock(side_effect=['connect', 'pending'])
        button = Mock()
        c._button = Mock(return_value=button)
        result = c.submit_no_note(URL, 'Fixture Person')
        self.assertEqual(result['evidence'], 'pending_after_submit')
        button.click.assert_called_once()
        c._button.assert_called_once_with(c._top.return_value, 'Connect')

    def test_changed_name_stops_before_click(self):
        c = self.controller()
        c._button = Mock()
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Different Person')
        c._button.assert_not_called()

    def test_ambiguous_dialog_stops_after_single_connect_no_retry(self):
        c = self.controller()
        c._state = Mock(return_value='connect')
        c._visible = Mock(return_value=[Mock(), Mock()])
        button = Mock()
        c._button = Mock(return_value=button)
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Fixture Person')
        button.click.assert_called_once()

    def test_wrong_dialog_recipient_never_clicks_send(self):
        c = self.controller()
        c._state = Mock(return_value='connect')
        c._visible = Mock(return_value=[Mock(text='Invite Different Person')])
        button = Mock()
        c._button = Mock(return_value=button)
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Fixture Person')
        c._button.assert_called_once()
        button.click.assert_called_once()

    def test_note_field_never_clicks_send(self):
        c = self.controller()
        c._state = Mock(return_value='connect')
        dialog = Mock(text='Invite Fixture Person')
        c._visible = Mock(side_effect=[[dialog], [Mock()]])
        c._button = Mock(return_value=Mock())
        with self.assertRaises(ControlError):
            c.submit_no_note(URL, 'Fixture Person')
        c._button.assert_called_once()

    def test_no_note_dialog_sends_once_then_observes_pending(self):
        c = self.controller()
        c._state = Mock(side_effect=['connect', 'connect', 'pending'])
        dialog = Mock(text='Invite Fixture Person')
        c._visible = Mock(side_effect=[[dialog], []])
        connect, send = Mock(), Mock()
        c._button = Mock(side_effect=[connect, send])
        result = c.submit_no_note(URL, 'Fixture Person')
        self.assertEqual(result['status'], 'confirmed')
        connect.click.assert_called_once()
        send.click.assert_called_once()
        self.assertEqual(c._button.call_args_list[-1].args[1], 'Send without a note')

    def test_checkpoint_during_manual_login_stops(self):
        c = self.controller()
        c.driver.current_url = 'https://www.linkedin.com/checkpoint/challenge/'
        with self.assertRaises(ControlError):
            wait_for_manual_login(c, 1)

    def test_foreign_origin_is_not_authenticated(self):
        c = SeleniumController(Mock(current_url='https://example.org/feed/'))
        self.assertEqual(c.safety_state(), 'unexpected_origin')

    def test_unique_enabled_button_required(self):
        c = self.controller()
        c._visible = Mock(return_value=[Mock(text='Connect'), Mock(text='Connect')])
        with self.assertRaises(ControlError):
            c._button(c.driver, 'Connect')


if __name__ == '__main__':
    unittest.main()
