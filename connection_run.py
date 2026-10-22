"""One bounded invitation run through an independently verified controller.

This engine never imports the credential-bearing legacy script.
Adapters must inspect identities read-only and implement
only a no-note invitation action; follow/message methods are not part of the API.
"""
import fcntl
import json
import os
import sys
import re
from itertools import islice
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs
from run_config import validate_run_bounds, build_search_url


def profile_url(value):
    url = urlsplit(value)
    parts = url.path.strip('/').split('/')
    if url.scheme != 'https' or url.hostname != 'www.linkedin.com' or len(parts) != 2 or parts[0] != 'in' or not parts[1]:
        raise ValueError('Unverified profile URL')
    return 'https://www.linkedin.com/in/' + parts[1] + '/'


def scope_matches(value, search_url):
    url = urlsplit(value)
    query = parse_qs(url.query)
    expected = parse_qs(urlsplit(search_url).query)
    try:
        return (url.scheme == 'https' and url.hostname == 'www.linkedin.com'
                and url.path == '/search/results/people/'
                and len(expected.get('keywords', [])) == 1 and bool(expected['keywords'][0].strip())
                and len(expected.get('network', [])) == 1
                and query.get('keywords') == expected.get('keywords')
                and len(query.get('network', [])) == 1
                and json.loads(query['network'][0]) == json.loads(expected['network'][0])
                and not (set(query) - {'keywords', 'network', 'page', 'origin', 'sid'}))
    except (ValueError, TypeError):
        return False


def summary(events, run_id):
    mine = [e for e in events if e['run_id'] == run_id]
    reserved = {e['profile_url'] for e in mine if e['status'] == 'attempt_reserved'}
    final = {e['profile_url']: e['status'] for e in mine if e['status'] in ('confirmed', 'not_sent', 'uncertain', 'skipped_email_required')}
    return {'attempted_or_reserved': len(reserved),
            'confirmed': sum(v == 'confirmed' for v in final.values()),
            'failed': sum(v == 'not_sent' for v in final.values()),
            'uncertain': sum(final.get(url) in (None, 'uncertain') for url in reserved),
            'skipped': sum(e['status'] in ('skipped', 'skipped_email_required') for e in mine)}


def exception_details(error):
    """Code locations only, never exception messages, source lines or locals."""
    import traceback
    return {'failure_class': type(error).__name__,
            'failure_location': [{'file': Path(frame.f_code.co_filename).name,
                                  'function': frame.f_code.co_name, 'line': line}
                                 for frame, line in traceback.walk_tb(error.__traceback__)]}


def verification_diagnostic(controller):
    """Only non-content observations and generated evidence filenames leave here."""
    diagnostic = getattr(controller, 'verification_diagnostic', {})
    if not isinstance(diagnostic, dict):
        return {}
    counts = {'nav_container_count', 'nav_menu_count', 'nav_primary_count', 'profile_heading_count', 'connection_control_count', 'invitation_dialog_count', 'search_unverified_card_count',
              'password_field_count', 'alert_count', 'authentication_control_count', 'login_read_timeout_count'}
    stages = {'controller_created', 'account_navigation_check', 'self_profile_redirect',
              'self_profile_identity', 'self_profile_heading', 'account_verified',
              'login_wait_passed', 'login_wait_timeout', 'login_checkpoint', 'login_wait_poll',
              'login_observation_timeout', 'login_observation_pending', 'invitation_precheck',
              'invitation_connect_clicked', 'invitation_dialog_check', 'invitation_send_clicked', 'invitation_pending', 'invitation_confirmed', 'invitation_email_skipped'}
    routes = {'checkpoint', 'login', 'self_alias', 'profile', 'feed', 'other'}
    states = {'ok', 'unexpected_origin', 'authentication_or_checkpoint', 'warning'}
    result = {k: v for k, v in diagnostic.items() if k in counts and type(v) is int and v >= 0}
    if type(diagnostic.get('failure_screenshot_saved')) is bool:
        result['failure_screenshot_saved'] = diagnostic['failure_screenshot_saved']
    screenshot = diagnostic.get('failure_screenshot')
    if isinstance(screenshot, str) and re.fullmatch(r'submission-failure-[0-9a-f]{32}\.png', screenshot):
        result['failure_screenshot'] = screenshot
    reads = {'current_route', 'navigation_container', 'navigation_menu', 'navigation_primary', 'profile_heading', 'connection_controls',
             'password_presence', 'authentication_controls', 'warning_controls', 'other_elements'}
    for key, allowed in [('stage', stages), ('route_category', routes), ('safety_state', states), ('last_read', reads), ('route_value_type', {'str', 'NoneType', 'bytes', 'other'}), ('connection_state', {'connect','pending','connected','message','follow','unknown'})]:
        if isinstance(diagnostic.get(key), str) and diagnostic[key] in allowed:
            result[key] = diagnostic[key]
    return result


def run_connections(controller, journal_path, run_id, limit, max_pages, max_candidates, keywords, network):
    """The adapter is trusted code backed by a supported browser control route.

    inspect_session, safety_state, iter_candidates and verify_candidate are read-
    only. submit_no_note is invoked once after a durable attempt reservation.
    It must verify the exact profile again before acting, never type a note,
    never follow/message, and return confirmed only after observing Pending or
    a fresh, recipient-bound invitation-success receipt after submission, or an
    exact matching pending entry in Sent invitations after the Send click.
    """
    validate_run_bounds(limit, max_pages, max_candidates)
    search_url = build_search_url(keywords, network)
    base = {'run_id': run_id, 'keywords': keywords, 'network': network,
            'limit': limit, 'max_pages': max_pages, 'max_candidates': max_candidates,
            'login_verified': False, 'follow_actions': 0, 'message_actions': 0}
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError('Explicit one-run identity is required')
    if controller is None:
        return dict(base, result='CONTROLLER_REQUIRED', attempted_or_reserved=0,
                    confirmed=0, skipped=0, failed=0, uncertain=0,
                    next='No live controller is wired into this CLI. Restore supported browser control and independently verify the logged-in account before running invitations. No browser action or ledger write occurred.')
    try:
        session = controller.inspect_session()
        account = profile_url(session.get('account_profile_url', ''))
        if session.get('authenticated') is not True or not session.get('account_name', '').strip() or controller.safety_state() != 'ok':
            raise ValueError('Account not independently verified')
    except Exception as error:
        return dict(base, result='AUTH_OR_CONTROL_UNVERIFIED', **exception_details(error),
                    verification=verification_diagnostic(controller), attempted_or_reserved=0,
                    confirmed=0, skipped=0, failed=0, uncertain=0)
    base.update(login_verified=True, account_name=session['account_name'], account_profile_url=account)
    path = Path(journal_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'a+', encoding='utf-8') as journal:
        fcntl.flock(journal.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal.seek(0)
        events = [json.loads(line) for line in journal if line.strip()]
        for event in events:
            if not isinstance(event, dict) or not {'run_id', 'status', 'profile_url'} <= event.keys():
                raise ValueError('Invalid receipt journal; refusing submission')
        attempted_profiles = {e['profile_url'] for e in events if e['status'] == 'attempt_reserved'}

        def record(status, url='', name='', reason=''):
            event = {'timestamp': datetime.now(timezone.utc).isoformat(), 'run_id': run_id,
                     'status': status, 'profile_url': url, 'name': name, 'reason': reason,
                     'account_profile_url': account}
            journal.write(json.dumps(event, ensure_ascii=False) + '\n')
            journal.flush()
            os.fsync(journal.fileno())
            events.append(event)
            if status in ('attempt_reserved', 'confirmed', 'not_sent', 'uncertain', 'skipped_email_required'):
                counts = summary(events, run_id)
                print('Connections: %s/%s attempts reserved; %s confirmed. Latest: %s.' %
                      (counts['attempted_or_reserved'], limit, counts['confirmed'], status), file=sys.stderr, flush=True)

        reviewed = 0

        def finish(result, error=None):
            report = dict(base, result=result, candidates_reviewed=reviewed,
                          verification=verification_diagnostic(controller), **summary(events, run_id))
            if error is not None:
                report.update(exception_details(error))
            return report

        if any(e['run_id'] == run_id and e.get('account_profile_url') != account for e in events):
            return finish('STOPPED_ACCOUNT_CHANGED')
        # An unresolved reservation may have reached the server. Do not resume
        # such a run or automatically retry it after a crash/control failure.
        if summary(events, run_id)['uncertain']:
            return finish('STOPPED_UNCERTAIN_PRIOR_ATTEMPT')
        seen = set()
        try:
            if summary(events, run_id)['attempted_or_reserved'] >= limit:
                return finish('ATTEMPT_CAP_REACHED')
            candidates = controller.iter_candidates(search_url, max_pages=max_pages)
            for candidate in islice(candidates, max_candidates):
                reviewed += 1
                if controller.safety_state() != 'ok':
                    return finish('STOPPED_CHECKPOINT_OR_WARNING')
                if not scope_matches(candidate.get('search_url', ''), search_url):
                    return finish('STOPPED_UNEXPECTED_SEARCH_SCOPE')
                url = profile_url(candidate.get('profile_url', ''))
                name = candidate.get('name', '').strip()
                if not name:
                    return finish('STOPPED_UNVERIFIED_IDENTITY')
                if url in seen or url in attempted_profiles or url == account:
                    record('skipped', url, name, 'duplicate_prior_attempt_or_self')
                    continue
                seen.add(url)
                state = candidate.get('state')
                if state in ('pending', 'connected', 'follow', 'message'):
                    record('skipped', url, name, state)
                    continue
                if state != 'connect':
                    record('skipped', url, name, 'unknown_connection_state')
                    continue
                identity = controller.verify_candidate(url)
                if (profile_url(identity.get('profile_url', '')) != url
                        or identity.get('name', '').strip().casefold() != name.casefold()
                        or identity.get('state') != 'connect'):
                    return finish('STOPPED_IDENTITY_OR_STATE_CHANGED')
                if controller.safety_state() != 'ok':
                    return finish('STOPPED_CHECKPOINT_OR_WARNING')
                # Durable budget consumption precedes the first invitation UI
                # action, since even a Connect click can submit directly.
                record('attempt_reserved', url, name)
                attempted_profiles.add(url)
                submission_error = None
                try:
                    outcome = controller.submit_no_note(url, expected_name=name)
                    status = outcome.get('status')
                    if profile_url(outcome.get('profile_url', '')) != url:
                        status = 'uncertain'
                    if status == 'confirmed' and outcome.get('evidence') not in ('pending_after_submit', 'invitation_sent_after_submit', 'sent_invitation_list_after_submit'):
                        status = 'uncertain'
                    if status == 'skipped_email_required' and outcome.get('evidence') != 'email_required_before_send':
                        status = 'uncertain'
                    if status not in ('confirmed', 'not_sent', 'skipped_email_required'):
                        status = 'uncertain'
                except Exception as error:
                    submission_error = error
                    status = 'uncertain'
                evidence = outcome.get('evidence') if status in ('confirmed', 'skipped_email_required') else 'no_note_invitation_submission'
                record(status, url, name, evidence)
                if status == 'uncertain':
                    return finish('STOPPED_UNCERTAIN_SUBMISSION', submission_error)
                if controller.safety_state() != 'ok':
                    return finish('STOPPED_CHECKPOINT_OR_WARNING')
                if summary(events, run_id)['attempted_or_reserved'] >= limit:
                    return finish('ATTEMPT_CAP_REACHED')
            return finish('REVIEW_BOUND_REACHED' if reviewed >= max_candidates else 'CANDIDATES_EXHAUSTED')
        except Exception as error:
            return dict(finish('STOPPED_CONTROL_OR_SELECTOR_ERROR'), **exception_details(error))
