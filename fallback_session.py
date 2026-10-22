"""Separate temporary/private browser session and manual-login handoff.

No authentication data is copied. Login observations must come from a supported
controller; a user saying 'logged in' alone is not a verified observation.
"""
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

LOGIN_URL = 'https://www.linkedin.com/login/'


def observe_login(observer=None, timeout_seconds=30):
    """Adapter boundary for supported browser control, absent in the stock CLI."""
    result = {'login_verified': False, 'automation_ready': False,
              'read_only_readiness_resumed': False, 'login_state': 'observation_required'}
    if observer is None:
        return result
    try:
        page = observer(timeout_seconds=timeout_seconds)
        if not isinstance(page, dict):
            raise ValueError('Invalid observation')
        url = urlsplit(page.get('url', ''))
        if url.scheme != 'https' or url.hostname != 'www.linkedin.com':
            return result
        if page.get('login_form_present') is True:
            result['login_state'] = 'login_needed'
        elif (url.path.startswith('/feed/') and page.get('account_menu_present') is True
              and page.get('feed_navigation_present') is True
              and page.get('login_form_present') is False):
            result.update(login_verified=True, automation_ready=True,
                          read_only_readiness_resumed=True, login_state='verified')
    except TimeoutError:
        result['login_state'] = 'observation_timeout'
    except Exception:
        result['login_state'] = 'observation_unavailable'
    return result


def prepare_private_session(shared_report, root, chrome, read_processes, match_pids):
    """Choose an isolated profile, or hand off an already-running fallback."""
    record = Path(root) / '.connectrobo-fallback.json'
    profile = None
    if record.exists():
        try:
            saved = json.loads(record.read_text())
            candidate = Path(saved['user_data_dir'])
            if (candidate.parent.resolve() != Path(tempfile.gettempdir()).resolve()
                    or not candidate.name.startswith('connectrobo-session-')
                    or candidate.is_symlink()):
                raise ValueError('Invalid temporary profile')
            if candidate.is_dir():
                profile = candidate.resolve()
        except (OSError, KeyError, ValueError, TypeError):
            raise ValueError('Cannot validate local fallback record; no browser launched. Inspect the local metadata file, never profile credentials.')
    # Process visibility is required even for a fresh temporary profile.
    process_text = read_processes()
    if profile is not None:
        pids = match_pids(profile, process_text)
        locks = [name for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie')
                 if (profile / name).exists() or (profile / name).is_symlink()]
        if pids or locks:
            report = dict(shared_report)
            report.update(user_data_dir=str(profile), profile_kind='temporary_private_fallback',
                          shared_user_data_dir=shared_report['user_data_dir'],
                          profile_state='running' if pids else 'locked_without_matching_process',
                          browser_open=True if pids else None, can_launch_new_instance=False,
                          matching_process_ids=pids, lock_markers=locks,
                          result='PRIVATE_BROWSER_ALREADY_OPEN' if pids else 'PRIVATE_PROFILE_LOCKED',
                          login_state='observation_required',
                          next='Use the existing ConnectRobo private window. Sign in manually if needed, complete MFA, and request supported browser observation before read-only readiness continues. No new browser launched.')
            # Never expose primary launch arguments as instructions for fallback.
            report.pop('launch_arguments', None)
            return report
    profile = Path(tempfile.mkdtemp(prefix='connectrobo-session-')).resolve()
    record.write_text(json.dumps({'user_data_dir': str(profile), 'mode': 'temporary_private'}) + '\n')
    record.chmod(0o600)
    report = dict(shared_report)
    report.update(user_data_dir=str(profile), profile_kind='temporary_private_fallback',
                  shared_user_data_dir=shared_report['user_data_dir'],
                  profile_state='idle', profile_idle=True, browser_open=False,
                  can_launch_new_instance=True, matching_process_ids=[], lock_markers=[],
                  login_state='login_needed',
                  launch_arguments=[str(chrome), '--new-window', '--no-first-run',
                                    '--user-data-dir=' + str(profile), '--incognito', LOGIN_URL])
    return report
