import json
from pathlib import Path
import tempfile
import unittest

from connection_run import run_connections
from run_config import build_search_url

SEARCH_URL = build_search_url('founder iit bay area', ['S'])


def candidate(index, state='connect'):
    return {'name': 'Synthetic Person %s' % index,
            'profile_url': 'https://www.linkedin.com/in/synthetic-%s/' % index,
            'search_url': SEARCH_URL, 'state': state}


class Controller:
    """Offline fixture only; no browser, network, credentials or real people."""
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []
        self.warning = 'ok'
        self.authenticated = True

    def inspect_session(self):
        return {'authenticated': self.authenticated, 'account_name': 'Synthetic Account',
                'account_profile_url': 'https://www.linkedin.com/in/synthetic-account/'}

    def safety_state(self):
        return self.warning

    def iter_candidates(self, url, max_pages):
        assert url == SEARCH_URL
        self.max_pages = max_pages
        yield from self.candidates

    def verify_candidate(self, url):
        return next(c for c in self.candidates if c['profile_url'] == url)

    def submit_no_note(self, url, expected_name):
        self.calls.append((url, expected_name))
        return {'status': 'confirmed', 'profile_url': url, 'evidence': 'pending_after_submit'}


class ConnectionRunTests(unittest.TestCase):
    def run_batch(self, controller, path, run_id, limit=20, max_pages=5, max_candidates=100):
        return run_connections(controller, path, run_id, limit, max_pages, max_candidates, 'founder iit bay area', ['S'])

    def test_email_required_skip_is_final_and_next_candidate_is_sent(self):
        c = Controller([candidate(1), candidate(2)])
        original = c.submit_no_note
        def submit(url, expected_name):
            if url == candidate(1)['profile_url']:
                c.calls.append((url, expected_name))
                return dict(status='skipped_email_required', profile_url=url, evidence='email_required_before_send')
            return original(url, expected_name)
        c.submit_no_note = submit
        result = self.run_batch(c, self.path, 'run')
        self.assertEqual(result['confirmed'], 1)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['uncertain'], 0)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['attempted_or_reserved'], 2)
        self.assertEqual(len(c.calls), 2)
        self.assertEqual(result['result'], 'CANDIDATES_EXHAUSTED')
        second = self.run_batch(c, self.path, 'next')
        self.assertEqual(second['skipped'], 2)
        self.assertEqual(len(c.calls), 2)
        resumed = self.run_batch(c, self.path, 'run')
        self.assertNotEqual(resumed['result'], 'STOPPED_UNCERTAIN_PRIOR_ATTEMPT')

    def test_email_skip_cannot_exceed_attempt_cap_or_accept_unverified_evidence(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                c = Controller([candidate(1), candidate(2)])
                def submit(url, expected_name):
                    c.calls.append((url, expected_name))
                    return dict(status='skipped_email_required', profile_url=url,
                                evidence='email_required_before_send' if valid else 'unknown')
                c.submit_no_note = submit
                result = self.run_batch(c, self.path, 'run-' + str(valid), limit=1)
                self.assertEqual(result['result'], 'ATTEMPT_CAP_REACHED' if valid else 'STOPPED_UNCERTAIN_SUBMISSION')
                self.assertEqual(result['uncertain'], 0 if valid else 1)
                self.assertEqual(len(c.calls), 1)
                self.path.unlink()

    def test_fifty_attempts_continue_past_skips_and_prior_attempts(self):
        self.run_batch(Controller([candidate(i) for i in range(23)]), self.path, 'prior', limit=23)
        skipped = [candidate(i) for i in range(23)]
        skipped += [candidate(i, 'message') for i in range(23, 40)]
        skipped += [candidate(i, 'follow') for i in range(40, 47)]
        skipped += [candidate(i, 'unknown') for i in range(47, 49)]
        c = Controller(skipped + [candidate(i) for i in range(49, 100)])
        result = self.run_batch(c, self.path, 'next', limit=50, max_pages=20, max_candidates=200)
        self.assertEqual(result['result'], 'ATTEMPT_CAP_REACHED')
        self.assertEqual(result['confirmed'], 50)
        self.assertEqual(result['skipped'], 49)
        self.assertEqual(result['candidates_reviewed'], 99)
        self.assertEqual(c.max_pages, 20)
        self.assertEqual(len(c.calls), 50)

    def test_candidate_boundary_stops_without_fetching_candidate_201(self):
        c = Controller([])
        def candidates(url, max_pages):
            for i in range(200):
                yield candidate(i, 'pending')
            self.fail('Fetched a candidate beyond the configured review boundary')
        c.iter_candidates = candidates
        result = self.run_batch(c, self.path, 'run', limit=50, max_pages=20, max_candidates=200)
        self.assertEqual(result['result'], 'REVIEW_BOUND_REACHED')
        self.assertEqual(result['skipped'], 200)
        self.assertEqual(result['candidates_reviewed'], 200)
        self.assertEqual(c.calls, [])

    def test_attempt_cap_does_not_fetch_another_page_or_candidate(self):
        c = Controller([candidate(1)])
        def candidates(url, max_pages):
            yield candidate(1)
            self.fail('Fetched another result after reaching the attempt cap')
        c.iter_candidates = candidates
        result = self.run_batch(c, self.path, 'run', limit=1, max_pages=20, max_candidates=200)
        self.assertEqual(result['result'], 'ATTEMPT_CAP_REACHED')
        self.assertEqual(result['confirmed'], 1)

    def test_custom_search_is_used_and_changed_filters_stop_before_sending(self):
        search = build_search_url('robotics founder', ['S', 'O'])
        for actual_search in (search, SEARCH_URL):
            with self.subTest(actual_search=actual_search):
                person = dict(candidate(1), search_url=actual_search)
                c = Controller([person])
                def candidates(url, max_pages):
                    self.assertEqual(url, search)
                    self.assertEqual(max_pages, 20)
                    yield person
                c.iter_candidates = candidates
                result = run_connections(c, self.path, actual_search, limit=50, max_pages=20,
                                         max_candidates=200, keywords='robotics founder', network=['S', 'O'])
                self.assertEqual(result['keywords'], 'robotics founder')
                self.assertEqual(result['network'], ['S', 'O'])
                if actual_search == search:
                    self.assertEqual(result['confirmed'], 1)
                else:
                    self.assertEqual(result['result'], 'STOPPED_UNEXPECTED_SEARCH_SCOPE')
                    self.assertEqual(c.calls, [])

    def test_unknown_recipient_is_skipped_without_an_action_or_blocking_verified_connect(self):
        c = Controller([candidate(0, 'unknown'), candidate(1)])
        result = self.run_batch(c, self.path, 'run')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(result['confirmed'], 1)
        self.assertEqual([url for url, _ in c.calls], [candidate(1)['profile_url']])

    def test_explicit_post_submit_receipt_counts_as_confirmed(self):
        c = Controller([candidate(1)])
        c.submit_no_note = lambda url, **kwargs: {'status':'confirmed', 'profile_url':url, 'evidence':'invitation_sent_after_submit'}
        result = self.run_batch(c, self.path, 'run')
        self.assertEqual(result['confirmed'], 1)
        events = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(events[-1]['reason'], 'invitation_sent_after_submit')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'receipts.jsonl'

    def test_cap_is_twenty_submission_calls_and_confirmations(self):
        c = Controller([candidate(i) for i in range(35)])
        r = self.run_batch(c, self.path, 'run')
        self.assertEqual(len(c.calls), 20)
        self.assertEqual(r['attempted_or_reserved'], 20)
        self.assertEqual(r['confirmed'], 20)
        self.assertEqual(r['result'], 'ATTEMPT_CAP_REACHED')

    def test_no_follow_pending_connected_or_message_actions(self):
        c = Controller([candidate(i, state) for i, state in enumerate(['follow', 'pending', 'connected', 'message'])] + [candidate(5)])
        r = self.run_batch(c, self.path, 'run')
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(r['skipped'], 4)
        self.assertEqual(r['follow_actions'], 0)
        self.assertEqual(r['message_actions'], 0)

    def test_duplicates_within_and_across_runs_are_skipped(self):
        c = Controller([candidate(1), candidate(1)])
        first = self.run_batch(c, self.path, 'run-1')
        second = self.run_batch(c, self.path, 'run-2')
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(first['skipped'], 1)
        self.assertEqual(second['skipped'], 2)

    def test_attempt_receipt_is_durable_before_first_invitation_action(self):
        c = Controller([candidate(1)])
        original = c.submit_no_note
        def submit(url, expected_name):
            rows = [json.loads(line) for line in self.path.read_text().splitlines()]
            self.assertEqual(rows[-1]['status'], 'attempt_reserved')
            self.assertEqual(rows[-1]['profile_url'], url)
            return original(url, expected_name)
        c.submit_no_note = submit
        r = self.run_batch(c, self.path, 'run')
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual([r['status'] for r in rows], ['attempt_reserved', 'confirmed'])
        self.assertEqual(r['confirmed'], 1)
        self.assertTrue(rows[-1]['timestamp'])

    def test_uncertain_submission_stops_and_is_never_retried(self):
        c = Controller([candidate(1), candidate(2)])
        def submit(url, expected_name):
            c.calls.append((url, expected_name))
            raise TimeoutError('synthetic transport loss')
        c.submit_no_note = submit
        first = self.run_batch(c, self.path, 'run')
        second = self.run_batch(c, self.path, 'run')
        self.assertEqual(first['uncertain'], 1)
        self.assertEqual(first['confirmed'], 0)
        self.assertEqual(second['result'], 'STOPPED_UNCERTAIN_PRIOR_ATTEMPT')
        self.assertEqual(len(c.calls), 1)

    def test_unconfirmed_success_is_uncertain_not_a_receipt(self):
        c = Controller([candidate(1)])
        c.submit_no_note = lambda url, **kwargs: {'status': 'confirmed', 'profile_url': url}
        r = self.run_batch(c, self.path, 'run')
        self.assertEqual(r['uncertain'], 1)
        self.assertEqual(r['confirmed'], 0)

    def test_known_failure_consumes_budget_and_is_not_retried(self):
        c = Controller([candidate(1), candidate(2)])
        def submit(url, **kwargs):
            c.calls.append(url)
            return {'status': 'not_sent', 'profile_url': url}
        c.submit_no_note = submit
        r = self.run_batch(c, self.path, 'run', limit=1)
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(r['failed'], 1)
        self.assertEqual(r['attempted_or_reserved'], 1)

    def test_identity_change_stops_before_submission(self):
        c = Controller([candidate(1)])
        c.verify_candidate = lambda url: dict(candidate(2))
        r = self.run_batch(c, self.path, 'run')
        self.assertEqual(r['result'], 'STOPPED_IDENTITY_OR_STATE_CHANGED')
        self.assertEqual(c.calls, [])

    def test_extra_geography_or_changed_search_stops_before_submission(self):
        for suffix in ['&geoUrn=123', '&keywords=other']:
            p = candidate(1)
            p['search_url'] += suffix
            c = Controller([p])
            r = self.run_batch(c, self.path, 'run')
            self.assertEqual(r['result'], 'STOPPED_UNEXPECTED_SEARCH_SCOPE')
            self.assertEqual(c.calls, [])

    def test_auth_or_checkpoint_uncertainty_does_not_write_or_submit(self):
        for state in ['auth', 'warning']:
            c = Controller([candidate(1)])
            c.authenticated = state != 'auth'
            c.warning = 'checkpoint' if state == 'warning' else 'ok'
            r = self.run_batch(c, self.path, 'run')
            self.assertEqual(r['result'], 'AUTH_OR_CONTROL_UNVERIFIED')
            self.assertFalse(self.path.exists())
            self.assertEqual(c.calls, [])

    def test_warning_after_first_submission_stops_further_invitations(self):
        c = Controller([candidate(1), candidate(2)])
        original = c.submit_no_note
        def submit(url, **kwargs):
            outcome = original(url, **kwargs)
            c.warning = 'restriction'
            return outcome
        c.submit_no_note = submit
        r = self.run_batch(c, self.path, 'run')
        self.assertEqual(r['result'], 'STOPPED_CHECKPOINT_OR_WARNING')
        self.assertEqual(len(c.calls), 1)

    def test_missing_controller_is_explicit_and_does_not_create_ledger(self):
        r = self.run_batch(None, self.path, 'authorized-run')
        self.assertEqual(r['result'], 'CONTROLLER_REQUIRED')
        self.assertEqual(r['attempted_or_reserved'], 0)
        self.assertFalse(self.path.exists())

    def test_invalid_cap_is_rejected(self):
        for limit in [0, -1, True, 1.5]:
            with self.assertRaises(ValueError):
                self.run_batch(None, self.path, 'run', limit)

    def test_crash_reservation_without_outcome_never_resumes_or_retries(self):
        event = {'run_id': 'run', 'status': 'attempt_reserved',
                 'profile_url': candidate(1)['profile_url'],
                 'account_profile_url': 'https://www.linkedin.com/in/synthetic-account/'}
        self.path.write_text(json.dumps(event) + '\n')
        c = Controller([candidate(1), candidate(2)])
        result = self.run_batch(c, self.path, 'run')
        self.assertEqual(result['result'], 'STOPPED_UNCERTAIN_PRIOR_ATTEMPT')
        self.assertEqual(result['uncertain'], 1)
        self.assertEqual(c.calls, [])


if __name__ == '__main__':
    unittest.main()
