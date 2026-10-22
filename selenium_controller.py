"""Selenium adapter for the saved profile. No passwords, cookies or stealth.

Selectors require positive identity and action evidence. Invitation controls
may be in LinkedIn's interop shadow root; receipts are verified after submission.
"""
from pathlib import Path
import time
import json
import re
import sys
import tempfile
from uuid import uuid4
from urllib.parse import urlsplit, parse_qs
from connection_run import profile_url, scope_matches

PROFILE_HEADING = 'main h1, main a[componentkey^="ProfileVerificationTriggerRef-"] h2'
PROFILE_HEADING_FALLBACK = '(//main//section[@aria-label="Primary content"]//section)[1]//h2'
LOCAL_PROFILE_HEADING = 'h1, a[componentkey^="ProfileVerificationTriggerRef-"] h2'
ACTION_CONTROLS = 'button, a[role="button"], a[href*="/preload/"][href*="invite"], a[href*="/messaging/"]'
SEARCH_CARDS = 'main .reusable-search__result-container, main [data-testid="lazy-column"] [role="listitem"]:has(p > a[href*="/in/"])'
INVITATION_DIALOG = '[role="dialog"], dialog[open]'


def _route_category(path):
    path = path.lower()
    return ('checkpoint' if any(x in path for x in ('checkpoint', 'challenge', 'authwall'))
            else 'login' if '/login' in path
            else 'self_alias' if path.rstrip('/') == '/in/me'
            else 'profile' if path.startswith('/in/')
            else 'feed' if path.startswith('/feed') else 'other')


class ControlError(RuntimeError):
    pass


class ObservationPending(ControlError):
    pass


class ObservationTimeout(ControlError):
    pass


class ProfileUnavailable(ControlError):
    def __init__(self, next_step):
        super().__init__('Saved Chrome profile is not ready')
        self.next_step = next_step


class SeleniumController:
    def __init__(self, driver, timeout=15, evidence_dir=None):
        self.driver = driver
        self.timeout = timeout
        self.verification_diagnostic = {"stage": "controller_created"}
        self._confirmed_invitation_ack = None
        self.evidence_dir = Path(evidence_dir) if evidence_dir is not None else None

    def current_url(self):
        self.verification_diagnostic['last_read'] = 'current_route'
        value = self.driver.current_url
        self.verification_diagnostic['route_value_type'] = ('str' if isinstance(value, str)
                                                          else 'NoneType' if value is None
                                                          else 'bytes' if isinstance(value, bytes) else 'other')
        if not isinstance(value, str) or not value.strip():
            raise ObservationPending('Current route is not yet observable')
        return value

    def _wait(self, predicate):
        """Poll read-only predicates; never replay navigation or a UI action."""
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                value = predicate()
            except Exception as error:
                if not isinstance(error, ObservationPending) and type(error).__name__ not in ('StaleElementReferenceException', 'TimeoutException'):
                    raise
                value = None
            if value:
                return value
            if time.monotonic() >= deadline:
                raise ObservationTimeout('Observation timed out')
            time.sleep(.25)

    def _visible(self, root, selector, by='css selector'):
        aliases = {'#global-nav':'navigation_container', 'nav.global-nav__nav':'navigation_menu',
                   '[data-testid="primary-nav"]':'navigation_primary', ACTION_CONTROLS:'connection_controls',
                   'main h1':'profile_heading', PROFILE_HEADING:'profile_heading',
                   'input[type="password"]':'password_presence',
                   'input[type="password"], iframe[src*="captcha"], [id*="captcha"]':'authentication_controls',
                   '[role="alert"], .artdeco-inline-feedback--error':'warning_controls'}
        self.verification_diagnostic['last_read'] = aliases.get(selector, 'other_elements')
        elements = root.find_elements(by, selector)
        if not isinstance(elements, list):
            raise ObservationPending('Elements are not yet observable')
        return [e for e in elements if e.is_displayed()]

    def _one(self, root, selector):
        elements = self._visible(root, selector)
        if len(elements) != 1:
            raise ControlError('Selector was absent or ambiguous')
        return elements[0]

    def _profile_headings(self):
        headings = self._visible(self.driver, PROFILE_HEADING)
        if not headings:
            # Unverified profiles have a plain h2 in the first primary-content
            # card, without the verification-trigger wrapper. Keep the scope
            # out of About/Experience sections and recommendation sidebars.
            headings = self._visible(self.driver, PROFILE_HEADING_FALLBACK, 'xpath')
        self.verification_diagnostic['last_read'] = 'profile_heading'
        return headings

    def _profile_heading(self):
        headings = self._profile_headings()
        if not headings:
            raise ObservationPending('Profile heading has not rendered')
        if len(headings) != 1:
            raise ControlError('Profile heading is ambiguous')
        return headings[0]

    def _profile_name(self, top):
        headings = self._visible(top, LOCAL_PROFILE_HEADING)
        if headings:
            return self._one(top, LOCAL_PROFILE_HEADING).text.strip()
        return self._one(top, 'h2').text.strip()

    def safety_state(self):
        state = self._read_safety_state()
        self.verification_diagnostic['safety_state'] = state
        return state

    def _read_safety_state(self):
        self.verification_diagnostic['last_read'] = 'current_route'
        url = urlsplit(self.current_url())
        self.verification_diagnostic['route_category'] = _route_category(url.path)
        if url.scheme != 'https' or url.hostname != 'www.linkedin.com':
            return 'unexpected_origin'
        if any(part in url.path.lower() for part in ('/login', '/checkpoint', '/challenge', '/authwall')):
            return 'authentication_or_checkpoint'
        controls = self._visible(self.driver, 'input[type="password"], iframe[src*="captcha"], [id*="captcha"]')
        self.verification_diagnostic['authentication_control_count'] = len(controls)
        if controls:
            return 'authentication_or_checkpoint'
        alerts = self._visible(self.driver, '[role="alert"], .artdeco-inline-feedback--error')
        self.verification_diagnostic['alert_count'] = len(alerts)
        if any(e.text.strip() and e.text.strip() != getattr(self, '_confirmed_invitation_ack', None) for e in alerts):
            return 'warning'
        return 'ok'

    def _safe(self):
        if self.safety_state() != 'ok':
            raise ControlError('Authentication, warning or scope requires handoff')

    def navigation_ready(self):
        """Positive account UI evidence for both observed LinkedIn layouts."""
        containers = len(self._visible(self.driver, '#global-nav'))
        menus = len(self._visible(self.driver, 'nav.global-nav__nav'))
        primary = len(self._visible(self.driver, '[data-testid="primary-nav"]'))
        if containers > 1 or menus > 1 or primary > 1:
            raise ControlError('Account navigation is ambiguous')
        return containers == 1 or menus == 1 or primary == 1

    def observe_verification(self, stage):
        """Counts and route category only; no content, URLs or auth material."""
        self.verification_diagnostic.update(stage=stage, last_read='current_route')
        path = urlsplit(self.current_url()).path.lower()
        category = _route_category(path)
        diagnostic = {'stage': stage, 'route_category': category,
                      'safety_state': self.safety_state()}
        for key, selector in [('nav_container_count', '#global-nav'),
                              ('nav_menu_count', 'nav.global-nav__nav'),
                              ('nav_primary_count', '[data-testid="primary-nav"]'),
                              ('password_field_count', 'input[type="password"]'),
                              ('alert_count', '[role="alert"], .artdeco-inline-feedback--error')]:
            diagnostic[key] = len(self._visible(self.driver, selector))
        diagnostic['profile_heading_count'] = len(self._profile_headings())
        self.verification_diagnostic = diagnostic
        print(json.dumps({'verification': diagnostic}), file=sys.stderr, flush=True)
        return diagnostic

    def inspect_session(self):
        self.observe_verification('account_navigation_check')
        def ready():
            self._safe()
            return self.navigation_ready()
        self._wait(ready)
        self.driver.get('https://www.linkedin.com/in/me/')
        self.observe_verification('self_profile_redirect')
        self._wait(lambda: urlsplit(self.current_url()).path not in ('/in/me/', '/in/me'))
        self.observe_verification('self_profile_identity')
        self._safe()
        account = profile_url(self.current_url())
        self._wait(lambda: len(self._profile_headings()) == 1)
        self.observe_verification('self_profile_heading')
        name = self._profile_heading().text.strip()
        if not name:
            raise ControlError('Missing account name')
        self.observe_verification('account_verified')
        return dict(authenticated=True, account_profile_url=account, account_name=name)

    def _top(self):
        self._safe()
        if self._visible(self.driver, 'main h1'):
            return self._one(self.driver, 'main section:has(h1)')
        heading = self._profile_heading()
        # The current layout places the verified-name h2 in its own top card;
        # select its nearest section, excluding recommendations and sidebars.
        return heading.find_element('xpath', 'ancestor::section[1]')

    def _state(self, root):
        buttons = self._visible(root, ACTION_CONTROLS)
        # Current search results render Pending as a plain link to the search
        # page, with no button role or invitation URL. Observe it only; it must
        # never become an actionable control through _button().
        for anchor in self._visible(root, 'a[href]:not([role="button"])'):
            if anchor.text.strip().casefold() == 'pending' and anchor not in buttons:
                buttons.append(anchor)
        labels = [b.text.strip().casefold() for b in buttons]
        state = next((value for value in ('pending', 'connect', 'message', 'follow') if value in labels), 'unknown')
        self.verification_diagnostic.update(connection_control_count=len(buttons), connection_state=state)
        return state

    def _read_candidate(self, card):
        anchors = self._visible(card, '.entity-result__title-text a[href*="/in/"]')
        if anchors:
            if len(anchors) != 1:
                return None
            anchor = anchors[0]
            names = self._visible(anchor, 'span[aria-hidden="true"]')
            if len(names) != 1:
                return None
            name = names[0].text.strip()
        else:
            # The observed current search layout has a dedicated name link
            # inside a paragraph, separate from the image and mutual contacts.
            anchors = []
            for link in self._visible(card, 'p > a[href*="/in/"]'):
                href = link.get_attribute('href')
                if not isinstance(href, str):
                    continue
                try:
                    profile_url(href)
                except ValueError:
                    # Hiring/opportunity subpages can sit beside the name
                    # link. Only canonical /in/<slug>/ links identify people.
                    continue
                anchors.append(link)
            if len(anchors) != 1:
                return None
            anchor = anchors[0]
            name = anchor.text.strip()
        if not name:
            return None
        try:
            canonical = profile_url(anchor.get_attribute('href'))
        except ValueError:
            return None
        state = self._state(card)
        return dict(name=name, profile_url=canonical,
                    state=state, search_url=self.current_url())

    def _search_snapshot(self, search_url):
        self._safe()
        if not scope_matches(self.current_url(), search_url):
            raise ControlError('Search redirected outside authorized scope')
        cards = self._visible(self.driver, SEARCH_CARDS)
        if not cards:
            return None
        raw = [self._read_candidate(card) for card in cards[:100]]
        snapshot = [candidate for candidate in raw if candidate is not None]
        self.verification_diagnostic['search_unverified_card_count'] = len(raw) - len(snapshot)
        if not snapshot:
            raise ObservationPending('Search identities have not rendered')
        if all(candidate['state'] == 'unknown' for candidate in snapshot):
            raise ObservationPending('Search action controls have not rendered')
        next_buttons = self._visible(self.driver, 'button.artdeco-pagination__button--next:not([disabled]), button[data-testid="pagination-controls-next-button-visible"]:not([disabled])')
        return snapshot, any(button.is_enabled() for button in next_buttons)

    def iter_candidates(self, search_url, max_pages):
        if not scope_matches(search_url, search_url) or type(max_pages) is not int or max_pages < 1:
            raise ControlError('Unexpected search scope')
        for page in range(1, max_pages + 1):
            url = search_url + '&page=' + str(page)
            self.driver.get(url)
            self._wait(lambda: self._visible(self.driver, 'main'))
            self._safe()
            if not scope_matches(self.current_url(), search_url):
                raise ControlError('Search redirected outside authorized scope')
            snapshot, has_next = self._wait(lambda: self._search_snapshot(search_url))
            # Capture pagination before yielding; profile review navigates away.
            yield from snapshot
            if not has_next:
                return

    def verify_candidate(self, url):
        expected = profile_url(url)
        self.driver.get(expected)
        self._wait(self._profile_headings)
        def identity_ready():
            top = self._top()
            state = self._state(top)
            if state == 'unknown':
                raise ObservationPending('Profile connection controls have not rendered')
            return dict(profile_url=profile_url(self.current_url()),
                        name=self._profile_name(top), state=state)
        return self._wait(identity_ready)

    def _button(self, root, label):
        matches = [b for b in self._visible(root, ACTION_CONTROLS) if b.text.strip().casefold() == label.casefold() and b.is_enabled()]
        if len(matches) != 1:
            raise ControlError('Action button absent or ambiguous')
        return matches[0]

    def _invitation_context(self, expected, value=None):
        """Allow only this profile or its observed recipient-bound invite route."""
        url = urlsplit(self.current_url() if value is None else value)
        target = urlsplit(expected)
        if url.scheme != 'https' or url.hostname != 'www.linkedin.com':
            raise ControlError('Invitation origin changed')
        if url.path.rstrip('/') == target.path.rstrip('/'):
            return 'profile'
        query = parse_qs(url.query)
        if (url.path == '/preload/custom-invite/' and set(query) == {'vanityName'}
                and query['vanityName'] == [target.path.strip('/').split('/')[-1]]):
            return 'dialog'
        raise ControlError('Invitation recipient route changed')

    def _invitation_dialogs(self):
        dialogs = self._visible(self.driver, INVITATION_DIALOG)
        if dialogs:
            return dialogs
        # Current LinkedIn hosts its legacy invitation UI in an open shadow
        # root. Document CSS queries cannot cross that boundary. Inspect only
        # the observed interop outlet, keeping recipient and input checks.
        hosts = self.driver.find_elements('css selector', '#interop-outlet')
        if not isinstance(hosts, list):
            raise ObservationPending('Invitation host has not rendered')
        if len(hosts) > 1:
            raise ControlError('Invitation host is ambiguous')
        if not hosts:
            return []
        try:
            return self._visible(hosts[0].shadow_root, INVITATION_DIALOG)
        except Exception as error:
            if type(error).__name__ == 'NoSuchShadowRootException':
                raise ObservationPending('Invitation content has not rendered') from error
            raise

    def _ack_snapshot(self):
        return {(e.id, e.text.strip()) for e in self._visible(self.driver, '[role="alert"], .artdeco-inline-feedback--error')}

    def _invitation_ack(self, expected, expected_name, baseline):
        if profile_url(self.current_url()) != expected:
            raise ControlError('Profile changed during receipt verification')
        if self._profile_heading().text.strip().casefold() != expected_name.casefold():
            raise ControlError('Recipient changed during receipt verification')
        # LinkedIn can address compound given names, e.g. "Shubhankar Mihir"
        # for the independently verified full name "Shubhankar Mihir Seth".
        # Accept only whole leading words, never arbitrary substring matches.
        parts = expected_name.split()
        names = {expected_name.casefold()}
        names.update(' '.join(parts[:end]).casefold() for end in range(1, len(parts) + 1))
        names.update(alias.strip().casefold() for alias in re.findall(r'\(([^()]+)\)', expected_name) if alias.strip())
        for identity, text in self._ack_snapshot():
            if (identity, text) in baseline:
                continue
            prefix = 'Invitation sent to '
            if text.startswith(prefix) and text[len(prefix):].removesuffix('.').casefold() in names:
                self._confirmed_invitation_ack = text
                return True
        return False

    def _sent_invitation_receipt(self, expected, expected_name):
        """Read-only fallback after Send; never click Withdraw or send again."""
        self._safe()
        self.driver.get('https://www.linkedin.com/mynetwork/invitation-manager/sent/')
        def receipt():
            self._safe()
            route = urlsplit(self.current_url())
            if route.path.rstrip('/') != '/mynetwork/invitation-manager/sent':
                raise ControlError('Sent-invitation view changed')
            anchors = self._visible(self.driver, 'main [role="listitem"] a[href*="/in/"], main li a[href*="/in/"]')
            for anchor in anchors:
                try:
                    if profile_url(anchor.get_attribute('href')) != expected:
                        continue
                except ValueError:
                    continue
                rows = anchor.find_elements('xpath', 'ancestor::*[@role="listitem" or self::li][1]')
                if len(rows) != 1:
                    continue
                row = rows[0]
                if ' '.join(expected_name.split()).casefold() not in ' '.join(row.text.split()).casefold():
                    continue
                withdraw = [e for e in self._visible(row, 'button, a, [role="button"]') if e.text.strip() == 'Withdraw']
                if len(withdraw) == 1:
                    return True
            return False
        return self._wait(receipt)

    def submit_no_note(self, url, expected_name):
        try:
            return self._submit_no_note(url, expected_name)
        except Exception:
            self._capture_submission_failure()
            raise

    def _capture_submission_failure(self):
        """Keep a local screenshot; evidence errors must not mask the failure."""
        if self.evidence_dir is None:
            return
        self.verification_diagnostic['failure_screenshot_saved'] = False
        self.verification_diagnostic.pop('failure_screenshot', None)
        try:
            self.evidence_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            name = 'submission-failure-' + uuid4().hex + '.png'
            # Reserve with owner-only permissions before Chrome writes it.
            with tempfile.NamedTemporaryFile(dir=self.evidence_dir, suffix='.png', delete=False) as output:
                temporary = Path(output.name)
            try:
                if self.driver.save_screenshot(str(temporary)):
                    destination = self.evidence_dir / name
                    temporary.replace(destination)
                    self.verification_diagnostic.update(failure_screenshot_saved=True, failure_screenshot=name)
            finally:
                temporary.unlink(missing_ok=True)
        except Exception:
            pass

    def _submit_no_note(self, url, expected_name):
        self.verification_diagnostic['stage'] = 'invitation_precheck'
        expected = profile_url(url)
        # Do not navigate or act if identity changed between review and action.
        if profile_url(self.current_url()) != expected:
            raise ControlError('Profile changed before submission')
        top = self._top()
        if self._profile_name(top).casefold() != expected_name.casefold() or self._state(top) != 'connect':
            raise ControlError('Identity or connection state changed')
        connect = self._button(top, 'Connect')
        if connect.tag_name == 'a' and self._invitation_context(expected, connect.get_attribute('href')) != 'dialog':
            raise ControlError('Connect link recipient unverified')
        connect.click()  # May itself submit; never retry.
        self.verification_diagnostic['stage'] = 'invitation_connect_clicked'
        return self._finish_invitation(expected, expected_name)

    def _skip_email_requirement(self, dialog, expected, expected_name):
        text = ' '.join(dialog.text.split()).casefold()
        if 'to verify this member knows you, please enter their email to connect.' not in text:
            return None
        # This known dialog omits the recipient's name. Bind it instead to the
        # same exact profile and heading verified before our Connect click.
        if (self._invitation_context(expected) != 'profile'
                or self._profile_heading().text.strip().casefold() != expected_name.casefold()):
            raise ControlError('Email dialog recipient unverified')
        fields = self._visible(dialog, 'textarea, input:not([type="hidden"]), [contenteditable="true"]')
        if (len(fields) != 1 or fields[0].tag_name != 'input'
                or fields[0].get_attribute('type') not in ('email', 'text')
                or fields[0].get_attribute('value') not in ('', None)):
            raise ControlError('Email requirement is ambiguous')
        sends = [b for b in self._visible(dialog, ACTION_CONTROLS)
                 if b.text.strip().casefold() == 'send without a note']
        if len(sends) != 1 or sends[0].is_enabled():
            raise ControlError('Email requirement is not blocking Send')
        dismiss = [b for b in self._visible(dialog, 'button[aria-label="Dismiss"], button[aria-label="Close"], button[data-test-modal-close-btn]')
                   if b.is_enabled()]
        if len(dismiss) != 1:
            raise ControlError('Email dialog dismissal is ambiguous')
        self._safe()
        dismiss[0].click()  # Cancel only; never fill the field or click Send.
        def dismissed():
            self._safe()
            if self._invitation_context(expected) != 'profile':
                raise ControlError('Profile changed while dismissing email requirement')
            if self._profile_heading().text.strip().casefold() != expected_name.casefold():
                raise ControlError('Recipient changed while dismissing email requirement')
            if self._invitation_dialogs():
                return False
            if self._state(self._top()) != 'connect':
                raise ControlError('Unexpected state after dismissing email requirement')
            return True
        self._wait(dismissed)
        self.verification_diagnostic.update(stage='invitation_email_skipped', invitation_dialog_count=0)
        return dict(status='skipped_email_required', profile_url=expected,
                    evidence='email_required_before_send')

    def _finish_invitation(self, expected, expected_name):
        """Observe an already-open invite, send once, and confirm this profile."""
        sent = False
        baseline = set()
        def response():
            try:
                context = self._invitation_context(expected)
                if sent and context == 'profile' and self._invitation_ack(expected, expected_name, baseline):
                    self._safe()
                    return 'sent_ack'
                self._safe()
                if context == 'profile' and self._state(self._top()) == 'pending':
                    return 'pending'
                dialogs = self._invitation_dialogs()
                self.verification_diagnostic['invitation_dialog_count'] = len(dialogs)
                return dialogs[0] if len(dialogs) == 1 else None
            except ControlError:
                # Cover every confirmation read, including _top's nested
                # safety check. Only the same recipient's fresh success toast
                # can explain a warning; other alerts/checkpoints still stop.
                if (sent and self._invitation_context(expected) == 'profile' and self.safety_state() == 'warning'
                        and self._invitation_ack(expected, expected_name, baseline)):
                    self._safe()
                    return 'sent_ack'
                raise
        state = self._wait(response)
        if state != 'pending':
            self.verification_diagnostic['stage'] = 'invitation_dialog_check'
            dialog = state
            email_skip = self._skip_email_requirement(dialog, expected, expected_name)
            if email_skip is not None:
                return email_skip
            if expected_name.casefold() not in dialog.text.casefold():
                raise ControlError('Invitation dialog recipient unverified')
            if self._visible(dialog, 'textarea, input:not([type="hidden"]), [contenteditable="true"]'):
                raise ControlError('Note or email input requires handoff')
            baseline = self._ack_snapshot()
            self._button(dialog, 'Send without a note').click()  # Exactly once.
            sent = True
            self.verification_diagnostic['stage'] = 'invitation_send_clicked'
            def confirmation():
                observed = response()
                return observed if isinstance(observed, str) and observed in ('pending', 'sent_ack') else None
            try:
                state = self._wait(confirmation)
            except ObservationTimeout:
                if not self._sent_invitation_receipt(expected, expected_name):
                    raise
                state = 'sent_list'
        self.verification_diagnostic['stage'] = 'invitation_pending' if state == 'pending' else 'invitation_confirmed'
        return dict(status='confirmed', profile_url=expected,
                    evidence={'pending':'pending_after_submit', 'sent_ack':'invitation_sent_after_submit',
                              'sent_list':'sent_invitation_list_after_submit'}[state])


def wait_for_profile_idle(config_path, seconds=180, restart_browser=False):
    """Wait only for a visibly running profile; preserve unobservable locks."""
    from chrome_start import check_ready, request_profile_close, StartupError
    if restart_browser and seconds <= 0:
        raise ProfileUnavailable('Restarting Chrome requires a positive --profile-timeout so Chrome can finish closing.')
    deadline = time.monotonic() + seconds
    prompted = False
    close_requested = False
    while True:
        report = check_ready(config_path)
        if report['can_launch_new_instance']:
            if report['profile_state'] == 'stale_lock':
                print('Previous Chrome process has exited. Reopening the same saved profile; Chrome will recover its stale startup locks.', file=sys.stderr, flush=True)
            return report
        if report['profile_state'] == 'unknown':
            raise ProfileUnavailable('Chrome process inspection failed. Allow process inspection and retry; the saved profile was not changed.')
        if report['profile_state'] == 'running' and restart_browser and not close_requested:
            try:
                pid = request_profile_close(report)
            except StartupError as error:
                raise ProfileUnavailable(str(error)) from error
            close_requested = True
            print('Requested ConnectRobo Chrome (PID %s) to close. Waiting up to %s seconds to reopen the same saved profile; JobRobo stays open.' % (pid, seconds), file=sys.stderr, flush=True)
        elif report['profile_state'] != 'running' and not close_requested:
            raise ProfileUnavailable('The saved Chrome profile is locked or its process state cannot be inspected. Inspect Chrome manually; no lock files were removed.')
        if not prompted and not close_requested:
            print('ConnectRobo Chrome is already open. Close that Chrome instance to continue automatically; your saved login is retained. JobRobo can stay open. To close it automatically, cancel this run and rerun with --restart-browser.', file=sys.stderr, flush=True)
            prompted = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProfileUnavailable('Timed out waiting for ConnectRobo Chrome to close and release its profile. Close its Chrome instance manually, then rerun. No force kill or lock deletion was attempted; JobRobo can stay open.')
        time.sleep(min(1, remaining))


def create_controller(root, config_path=None, profile_timeout=0, restart_browser=False):
    """Only call after explicit authorization to use Selenium for browser UI."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from chrome_start import prepare_profile, startup_lease
    import os
    root = Path(root)
    # Selenium Manager uses official vendor driver discovery; no bundled driver.
    os.environ['SE_CACHE_PATH'] = str(root / '.selenium-cache')
    os.environ['SE_AVOID_STATS'] = 'true'
    os.environ['SE_SKIP_DRIVER_IN_PATH'] = 'true'
    os.environ['SE_AVOID_BROWSER_DOWNLOAD'] = 'true'
    with startup_lease(root):
        report = wait_for_profile_idle(config_path or root / 'chrome_session.json', profile_timeout,
                                       restart_browser=restart_browser)
        prepare_profile(report)
        options = Options()
        options.binary_location = report['launch_arguments'][0]
        for argument in report['launch_arguments'][1:-1]:
            options.add_argument(argument)
        driver = webdriver.Chrome(options=options)
    try:
        driver.set_page_load_timeout(30)
        driver.get('https://www.linkedin.com/feed/')
    except Exception:
        driver.quit()
        raise
    return SeleniumController(driver, evidence_dir=root / 'evidence')


def wait_for_manual_login(controller, seconds=180):
    deadline = time.monotonic() + seconds
    read_timeouts = 0
    while time.monotonic() < deadline:
        controller.verification_diagnostic.update(stage='login_wait_poll', last_read='current_route',
                                                 login_read_timeout_count=read_timeouts)
        try:
            path = urlsplit(controller.current_url()).path.lower()
            if any(p in path for p in ('checkpoint', 'challenge', 'authwall')):
                controller.observe_verification('login_checkpoint')
                raise ControlError('Checkpoint requires handoff')
            if controller.safety_state() == 'ok' and controller.navigation_ready():
                controller.observe_verification('login_wait_passed')
                return
        except Exception as error:
            # Navigation during manual sign-in can time out a read. Retry only
            # observation within the original deadline; never replay an action.
            if not isinstance(error, ObservationPending) and type(error).__name__ not in ('TimeoutException', 'StaleElementReferenceException'):
                raise
            read_timeouts += 1
            controller.verification_diagnostic.update(stage=('login_observation_timeout' if type(error).__name__ == 'TimeoutException' else 'login_observation_pending'),
                                                     login_read_timeout_count=read_timeouts)
            print(json.dumps({'verification': controller.verification_diagnostic}), file=sys.stderr, flush=True)
        time.sleep(.5)
    controller.verification_diagnostic.update(stage='login_wait_timeout',
                                             login_read_timeout_count=read_timeouts)
    print(json.dumps({'verification': controller.verification_diagnostic}), file=sys.stderr, flush=True)
    raise ControlError('Manual login not independently observed before timeout')
