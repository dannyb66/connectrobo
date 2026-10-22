import unittest
from unittest.mock import Mock, patch

from test_connection_run import SEARCH_URL
from selenium_controller import SeleniumController, ControlError, ObservationPending, PROFILE_HEADING, PROFILE_HEADING_FALLBACK


class StaleElementReferenceException(Exception):
    pass


class CurrentLayoutTests(unittest.TestCase):
    def search_card(self, name, slug, state, extra_links=()):
        anchor = Mock(text=name)
        anchor.get_attribute.return_value = 'https://www.linkedin.com/in/' + slug + '/'
        action = Mock(text=state)
        action.get_attribute.return_value = SEARCH_URL
        card = Mock()
        def elements(by, selector):
            if selector == 'p > a[href*="/in/"]':
                return [anchor, *extra_links]
            if selector == 'a[href]:not([role="button"])':
                return [anchor, action, *extra_links]
            return []
        card.find_elements.side_effect = elements
        return card, action

    def test_pending_plain_anchor_is_observed_without_clicking_or_waiting(self):
        card, pending = self.search_card('Fixture Person', 'fixture-person', 'Pending')
        controller = SeleniumController(Mock(current_url=SEARCH_URL), timeout=0)
        self.assertEqual(controller._read_candidate(card)['state'], 'pending')
        with self.assertRaises(ControlError):
            controller._button(card, 'Pending')
        pending.click.assert_not_called()

    def test_pending_search_page_advances_to_next_page_without_actions(self):
        from selenium_controller import SEARCH_CARDS
        first, pending = self.search_card('First Person', 'first-person', 'Pending')
        second, pending2 = self.search_card('Second Person', 'second-person', 'Pending')
        driver = Mock(current_url=SEARCH_URL)
        driver.get.side_effect = lambda url: setattr(driver, 'current_url', url)
        def elements(by, selector):
            if selector == 'main':
                return [Mock()]
            if selector == SEARCH_CARDS:
                return [second] if driver.current_url.endswith('page=2') else [first]
            if 'pagination' in selector and driver.current_url.endswith('page=1'):
                return [Mock(is_enabled=lambda: True)]
            return []
        driver.find_elements.side_effect = elements
        controller = SeleniumController(driver, timeout=0)
        results = list(controller.iter_candidates(SEARCH_URL, max_pages=2))
        self.assertEqual([candidate['state'] for candidate in results], ['pending', 'pending'])
        self.assertEqual(driver.get.call_count, 2)
        pending.click.assert_not_called()
        pending2.click.assert_not_called()

    def test_hiring_opportunity_link_is_not_treated_as_another_profile_identity(self):
        hiring = Mock(text='See open roles')
        hiring.get_attribute.return_value = 'https://www.linkedin.com/in/fixture-person/opportunities/hiring-opportunities/view/'
        card, pending = self.search_card('Fixture Person', 'fixture-person', 'Pending', [hiring])
        controller = SeleniumController(Mock(current_url=SEARCH_URL))
        candidate = controller._read_candidate(card)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate['name'], 'Fixture Person')
        self.assertEqual(candidate['profile_url'], 'https://www.linkedin.com/in/fixture-person/')
        hiring.click.assert_not_called()

    def test_sent_list_receipt_requires_matching_profile_name_and_withdraw_row(self):
        url = 'https://www.linkedin.com/in/fixture-person/'
        driver = Mock(current_url='https://www.linkedin.com/mynetwork/invitation-manager/sent/')
        controller = SeleniumController(driver, timeout=0)
        controller._safe = Mock()
        anchor = Mock()
        anchor.get_attribute.return_value = url
        row = Mock(text='Fixture Person\nSent today\nWithdraw')
        anchor.find_elements.return_value = [row]
        withdraw = Mock(text='Withdraw')
        controller._visible = Mock(side_effect=lambda root, selector: [anchor] if root is driver else [withdraw])
        self.assertTrue(controller._sent_invitation_receipt(url, 'Fixture Person'))
        driver.get.assert_called_once_with('https://www.linkedin.com/mynetwork/invitation-manager/sent/')
        withdraw.click.assert_not_called()
        row.text = 'Someone Else\nSent today\nWithdraw'
        with self.assertRaises(ControlError):
            controller._sent_invitation_receipt(url, 'Fixture Person')
        row.text = 'Fixture Person\nSent today\nWithdraw'
        anchor.get_attribute.return_value = 'https://www.linkedin.com/in/someone-else/'
        with self.assertRaises(ControlError):
            controller._sent_invitation_receipt(url, 'Fixture Person')

    def test_success_receipt_accepts_nickname_present_in_verified_display_name(self):
        url = 'https://www.linkedin.com/in/fixture-person/'
        controller = SeleniumController(Mock(current_url=url))
        controller._profile_heading = Mock(return_value=Mock(text='Fixture (Nick) Person'))
        controller._ack_snapshot = Mock(return_value={('toast-1', 'Invitation sent to Nick.')})
        self.assertTrue(controller._invitation_ack(url, 'Fixture (Nick) Person', set()))
        controller._ack_snapshot.return_value = {('toast-2', 'Invitation sent to Someone Else.')}
        self.assertFalse(controller._invitation_ack(url, 'Fixture (Nick) Person', set()))

    def test_fresh_recipient_bound_success_receipt_confirms_invitation(self):
        url = 'https://www.linkedin.com/in/fixture-person/'
        controller = SeleniumController(Mock(current_url=url))
        controller._profile_heading = Mock(return_value=Mock(text='Fixture Person'))
        controller._ack_snapshot = Mock(return_value={('toast-1', 'Invitation sent to Fixture.')})
        self.assertTrue(controller._invitation_ack(url, 'Fixture Person', set()))
        self.assertEqual(controller._confirmed_invitation_ack, 'Invitation sent to Fixture.')
        self.assertFalse(controller._invitation_ack(url, 'Fixture Person', {('toast-1', 'Invitation sent to Fixture.')}))

    def test_receipt_for_another_person_or_profile_is_rejected(self):
        url = 'https://www.linkedin.com/in/fixture-person/'
        controller = SeleniumController(Mock(current_url=url))
        controller._profile_heading = Mock(return_value=Mock(text='Fixture Person'))
        controller._ack_snapshot = Mock(return_value={('toast-1', 'Invitation sent to Someone Else.')})
        self.assertFalse(controller._invitation_ack(url, 'Fixture Person', set()))
        controller.driver.current_url = 'https://www.linkedin.com/in/someone-else/'
        with self.assertRaises(ControlError):
            controller._invitation_ack(url, 'Fixture Person', set())

    def test_only_independently_confirmed_success_toast_is_not_a_warning(self):
        success = Mock(text='Invitation sent to Fixture.')
        driver = Mock(current_url='https://www.linkedin.com/in/fixture-person/')
        driver.find_elements.side_effect = lambda by, selector: [success] if '[role="alert"]' in selector else []
        controller = SeleniumController(driver)
        self.assertEqual(controller.safety_state(), 'warning')
        controller._confirmed_invitation_ack = success.text
        self.assertEqual(controller.safety_state(), 'ok')
        success.text = 'Invitation limit reached'
        self.assertEqual(controller.safety_state(), 'warning')

    def test_invitation_dialog_is_found_inside_interop_shadow_root(self):
        dialog = Mock()
        hidden = Mock()
        hidden.is_displayed.return_value = False
        host = Mock()
        host.shadow_root.find_elements.return_value = [dialog]
        driver = Mock()
        driver.find_elements.side_effect = lambda by, selector: [host] if selector == '#interop-outlet' else [hidden]
        controller = SeleniumController(driver)
        self.assertEqual(controller._invitation_dialogs(), [dialog])
        dialog.click.assert_not_called()

    def test_ambiguous_shadow_host_never_exposes_a_send_control(self):
        driver = Mock()
        driver.find_elements.side_effect = lambda by, selector: [Mock(), Mock()] if selector == '#interop-outlet' else []
        with self.assertRaises(ControlError):
            SeleniumController(driver)._invitation_dialogs()

    def test_plain_h2_name_is_scoped_to_first_primary_profile_card(self):
        heading = Mock(text='Fixture Person')
        driver = Mock()
        driver.find_elements.side_effect = lambda by, selector: [heading] if by == 'xpath' and selector == PROFILE_HEADING_FALLBACK else []
        controller = SeleniumController(driver)
        self.assertIs(controller._profile_heading(), heading)
        controller._safe = Mock()
        self.assertIs(controller._top(), heading.find_element.return_value)

    def test_plain_h2_duplicate_profile_names_are_rejected(self):
        driver = Mock()
        driver.find_elements.side_effect = lambda by, selector: [Mock(), Mock()] if by == 'xpath' else []
        with self.assertRaises(ControlError):
            SeleniumController(driver)._profile_heading()

    def test_name_rendered_before_action_controls_waits_instead_of_yielding_unknown(self):
        anchor = Mock(text='Fixture Person')
        anchor.get_attribute.return_value = 'https://www.linkedin.com/in/fixture-person/'
        card = Mock()
        card.find_elements.side_effect = lambda by, selector: [anchor] if selector == 'p > a[href*="/in/"]' else []
        controller = SeleniumController(Mock(current_url=SEARCH_URL))
        controller._safe = Mock()
        controller._visible = Mock(side_effect=lambda root, selector: [] if 'pagination' in selector else [card])
        controller._read_candidate = Mock(side_effect=[{'state':'unknown'}, {'state':'connect'}])
        with patch('selenium_controller.time.sleep'):
            snapshot, _ = controller._wait(lambda: controller._search_snapshot(SEARCH_URL))
        self.assertEqual(snapshot[0]['state'], 'connect')
        self.assertEqual(controller._read_candidate.call_count, 2)
        anchor.click.assert_not_called()

    def test_persistently_unrecognized_controls_do_not_become_connect(self):
        anchor = Mock(text='Fixture Person')
        card = Mock()
        card.find_elements.side_effect = lambda by, selector: [anchor] if selector == 'p > a[href*="/in/"]' else []
        controller = SeleniumController(Mock(current_url=SEARCH_URL), timeout=0)
        controller._safe = Mock()
        controller._visible = Mock(side_effect=lambda root, selector: [] if 'pagination' in selector else [card])
        controller._read_candidate = Mock(return_value={'state':'unknown'})
        with self.assertRaises(ControlError):
            controller._wait(lambda: controller._search_snapshot(SEARCH_URL))
        anchor.click.assert_not_called()

    def test_one_unrecognized_result_does_not_block_verified_candidates(self):
        controller = SeleniumController(Mock(current_url=SEARCH_URL))
        controller._safe = Mock()
        controller._visible = Mock(side_effect=lambda root, selector: [] if 'pagination' in selector else [Mock(), Mock()])
        controller._read_candidate = Mock(side_effect=[{'state':'unknown'}, {'state':'connect'}])
        snapshot, _ = controller._search_snapshot(SEARCH_URL)
        self.assertEqual([candidate['state'] for candidate in snapshot], ['unknown', 'connect'])

    def test_profile_identity_waits_for_connection_controls_to_render(self):
        url = 'https://www.linkedin.com/in/fixture-person/'
        driver = Mock(current_url=url)
        controller = SeleniumController(driver)
        controller._visible = Mock(return_value=[Mock()])
        controller._top = Mock()
        controller._one = Mock(return_value=Mock(text='Fixture Person'))
        controller._state = Mock(side_effect=['unknown', 'connect'])
        with patch('selenium_controller.time.sleep'):
            identity = controller.verify_candidate(url)
        self.assertEqual(identity['state'], 'connect')
        self.assertEqual(controller._state.call_count, 2)
        driver.get.assert_called_once_with(url)

    def test_message_and_connect_anchors_are_recognized_without_clicking(self):
        for label in ('Message', 'Connect', 'Pending'):
            action = Mock(text=label)
            root = Mock()
            root.find_elements.side_effect = lambda by, selector: [action] if 'a[href' in selector else []
            controller = SeleniumController(Mock())
            self.assertEqual(controller._state(root), label.lower())
            self.assertIs(controller._button(root, label), action)
            action.click.assert_not_called()

    def test_disabled_or_ambiguous_anchor_never_becomes_an_action(self):
        controller = SeleniumController(Mock())
        disabled = Mock(text='Connect')
        disabled.is_enabled.return_value = False
        root = Mock()
        root.find_elements.return_value = [disabled]
        with self.assertRaises(ControlError):
            controller._button(root, 'Connect')
        first, second = Mock(text='Connect'), Mock(text='Connect')
        root.find_elements.return_value = [first, second]
        with self.assertRaises(ControlError):
            controller._button(root, 'Connect')
        first.click.assert_not_called()
        second.click.assert_not_called()

    def test_current_search_reads_dedicated_name_link(self):
        anchor = Mock(text='Fixture Person')
        anchor.get_attribute.return_value = 'https://www.linkedin.com/in/fixture-person/'
        card = Mock()
        card.find_elements.side_effect = lambda by, selector: [anchor] if selector == 'p > a[href*="/in/"]' else []
        controller = SeleniumController(Mock(current_url=SEARCH_URL))
        controller._state = Mock(return_value='connect')
        candidate = controller._read_candidate(card)
        self.assertEqual(candidate['name'], 'Fixture Person')
        self.assertEqual(candidate['profile_url'], 'https://www.linkedin.com/in/fixture-person/')
        self.assertEqual(candidate['state'], 'connect')
        anchor.click.assert_not_called()

    def test_ambiguous_search_names_fail_closed(self):
        card = Mock()
        card.find_elements.side_effect = lambda by, selector: [Mock(), Mock()] if selector == 'p > a[href*="/in/"]' else []
        self.assertIsNone(SeleniumController(Mock())._read_candidate(card))
        card.click.assert_not_called()

    def test_ambiguous_search_card_does_not_block_other_identified_results(self):
        controller = SeleniumController(Mock(current_url=SEARCH_URL))
        controller._safe = Mock()
        controller._visible = Mock(side_effect=lambda root, selector: [] if 'pagination' in selector else [Mock(), Mock()])
        controller._read_candidate = Mock(side_effect=[None, {'state':'connect'}])
        snapshot, _ = controller._search_snapshot(SEARCH_URL)
        self.assertEqual(snapshot, [{'state':'connect'}])
        self.assertEqual(controller.verification_diagnostic['search_unverified_card_count'], 1)

    def test_h2_top_card_uses_nearest_section(self):
        heading = Mock()
        driver = Mock()
        driver.find_elements.side_effect = lambda by, selector: [heading] if selector == PROFILE_HEADING else []
        controller = SeleniumController(driver)
        controller._safe = Mock()
        self.assertIs(controller._top(), heading.find_element.return_value)
        heading.find_element.assert_called_once_with('xpath', 'ancestor::section[1]')

    def test_read_only_wait_retries_transient_stale_element(self):
        controller = SeleniumController(Mock())
        predicate = Mock(side_effect=[StaleElementReferenceException(), 'observed'])
        with patch('selenium_controller.time.sleep'):
            self.assertEqual(controller._wait(predicate), 'observed')
        self.assertEqual(predicate.call_count, 2)

    def test_read_only_retry_does_not_extend_deadline_or_swallow_safety_failure(self):
        controller = SeleniumController(Mock(), timeout=1)
        predicate = Mock(side_effect=StaleElementReferenceException())
        with patch('selenium_controller.time.monotonic', side_effect=[0, 2]), self.assertRaises(ControlError):
            controller._wait(predicate)
        self.assertEqual(predicate.call_count, 1)
        with patch('selenium_controller.time.sleep') as sleep, self.assertRaises(ControlError):
            controller._wait(Mock(side_effect=ControlError('checkpoint')))
        sleep.assert_not_called()

    def test_search_snapshot_retry_does_not_repeat_navigation_or_actions(self):
        driver = Mock(current_url=SEARCH_URL)
        controller = SeleniumController(driver)
        controller._safe = Mock()
        card = Mock()
        controller._visible = Mock(side_effect=lambda root, selector: [] if 'pagination' in selector else [card])
        expected = dict(name='Fixture Person', profile_url='https://www.linkedin.com/in/fixture-person/',
                        state='connect', search_url=SEARCH_URL)
        controller._read_candidate = Mock(side_effect=[StaleElementReferenceException(), expected])
        with patch('selenium_controller.time.sleep'):
            self.assertEqual(list(controller.iter_candidates(SEARCH_URL, max_pages=1)), [expected])
        driver.get.assert_called_once_with(SEARCH_URL + '&page=1')
        self.assertEqual(controller._read_candidate.call_count, 2)
        card.click.assert_not_called()
