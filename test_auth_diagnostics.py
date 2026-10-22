import contextlib
import io
import unittest
from unittest.mock import Mock, patch
from selenium_controller import SeleniumController, ControlError
from connection_run import verification_diagnostic, run_connections
from pathlib import Path
import tempfile


class FakeAccountDriver:
    def __init__(self, containers=1, menus=1, headings=1):
        self.current_url = 'https://www.linkedin.com/feed/'
        self.navigations = []
        self.mapping = {'#global-nav': [Mock(is_displayed=lambda: True)] * containers,
                        'nav.global-nav__nav': [Mock(is_displayed=lambda: True)] * menus,
                        'main h1': [Mock(text='Fixture Account', is_displayed=lambda: True)] * headings}

    def find_elements(self, by, selector):
        if selector == 'main h1, main a[componentkey^="ProfileVerificationTriggerRef-"] h2':
            return self.mapping.get('main h1', []) + self.mapping.get('modern_heading', [])
        return self.mapping.get(selector, [])

    def get(self, url):
        self.navigations.append(url)
        self.current_url = 'https://www.linkedin.com/in/fixture-account/'


class AccountDiagnosticTests(unittest.TestCase):
    def test_current_navigation_and_h2_self_profile_are_verified(self):
        driver = FakeAccountDriver(containers=0, menus=0, headings=0)
        driver.mapping['[data-testid="primary-nav"]'] = [Mock(is_displayed=lambda: True)]
        driver.mapping['modern_heading'] = [Mock(text='Fixture Account', is_displayed=lambda: True)]
        controller = SeleniumController(driver, timeout=0)
        with contextlib.redirect_stderr(io.StringIO()):
            session = controller.inspect_session()
        self.assertTrue(session['authenticated'])
        self.assertEqual(session['account_name'], 'Fixture Account')
        self.assertEqual(controller.verification_diagnostic['nav_primary_count'], 1)
        self.assertEqual(controller.verification_diagnostic['profile_heading_count'], 1)

    def test_inspect_session_waits_for_navigation_before_self_profile(self):
        driver = FakeAccountDriver(containers=0, menus=0)
        controller = SeleniumController(driver)
        calls = 0
        original = driver.find_elements
        def delayed(by, selector):
            nonlocal calls
            if selector == '[data-testid="primary-nav"]':
                calls += 1
                return [Mock(is_displayed=lambda: True)] if calls >= 3 else []
            return original(by, selector)
        driver.find_elements = delayed
        with contextlib.redirect_stderr(io.StringIO()), patch('selenium_controller.time.sleep'):
            self.assertTrue(controller.inspect_session()['authenticated'])
        self.assertGreaterEqual(calls, 3)

    def test_duplicate_modern_navigation_never_verifies_account(self):
        driver = FakeAccountDriver(containers=0, menus=0)
        driver.mapping['[data-testid="primary-nav"]'] = [Mock(is_displayed=lambda: True)] * 2
        controller = SeleniumController(driver, timeout=0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ControlError):
            controller.inspect_session()
        self.assertFalse(driver.navigations)

    def test_nested_nav_container_and_menu_are_not_false_ambiguity(self):
        driver = FakeAccountDriver()
        c = SeleniumController(driver, timeout=0)
        with contextlib.redirect_stderr(io.StringIO()) as output:
            session = c.inspect_session()
        self.assertTrue(session['authenticated'])
        self.assertEqual(session['account_name'], 'Fixture Account')
        self.assertEqual(driver.navigations, ['https://www.linkedin.com/in/me/'])
        self.assertEqual(c.verification_diagnostic['stage'], 'account_verified')
        self.assertNotIn('Fixture Account', output.getvalue())
        self.assertNotIn('/in/fixture-account/', output.getvalue())

    def test_duplicate_container_fails_without_navigation(self):
        driver = FakeAccountDriver(containers=2)
        c = SeleniumController(driver, timeout=0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ControlError):
            c.inspect_session()
        self.assertFalse(driver.navigations)
        self.assertEqual(c.verification_diagnostic['nav_container_count'], 2)

    def test_missing_heading_fails_at_profile_identity(self):
        c = SeleniumController(FakeAccountDriver(headings=0), timeout=0)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(ControlError):
            c.inspect_session()
        self.assertEqual(c.verification_diagnostic['stage'], 'self_profile_identity')
        self.assertEqual(c.verification_diagnostic['profile_heading_count'], 0)

    def test_diagnostic_allowlist_excludes_content_and_untrusted_values(self):
        c = Mock(verification_diagnostic={'stage':'https://secret.invalid/token', 'password':'secret',
                                        'nav_menu_count':1,'alert_count':True,'route_category':'feed'})
        self.assertEqual(verification_diagnostic(c), {'nav_menu_count':1,'route_category':'feed'})

    def test_engine_retains_sanitized_reason_without_receipt_on_auth_failure(self):
        c = SeleniumController(FakeAccountDriver(containers=2), timeout=0)
        with tempfile.TemporaryDirectory() as root, contextlib.redirect_stderr(io.StringIO()):
            ledger = Path(root) / 'receipts.jsonl'
            result = run_connections(c, ledger, 'synthetic', limit=20, max_pages=5, max_candidates=100, keywords='founder iit bay area', network=['S'])
            self.assertFalse(ledger.exists())
        self.assertEqual(result['verification']['stage'], 'account_navigation_check')
        self.assertEqual(result['failure_class'], 'ControlError')
        self.assertEqual(result['attempted_or_reserved'], 0)
