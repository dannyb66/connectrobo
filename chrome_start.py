"""Browser-only startup for ConnectRobo. No login, invitation or messaging code."""
import argparse
from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time

from chrome_preflight import load_config, preflight
from fallback_session import observe_login, prepare_private_session

ROOT = Path(__file__).resolve().parent
CHROME = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
URL = 'https://www.linkedin.com/feed/'


class StartupError(RuntimeError):
    pass


def active_profile_pids(profile, process_text):
    """Match exact user-data-dir, including paths with spaces; retain only PIDs."""
    pattern = re.compile(r'--user-data-dir(?:=|\s+)(?:"([^"]+)"|\x27([^\x27]+)\x27|(.+?))(?=\s--|\shttps?://|$)')
    pids = []
    for line in process_text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            match = pattern.search(parts[1])
            if match:
                value = next(v for v in match.groups() if v is not None).strip()
                if Path(value).expanduser().resolve() == Path(profile).resolve():
                    pids.append(int(parts[0]))
    return pids


def read_processes():
    try:
        return subprocess.run(['/bin/ps', '-Aww', '-o', 'pid=,command='],
                              capture_output=True, text=True, check=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        # Do not expose process arguments, which may contain other applications' secrets.
        raise StartupError('Cannot inspect active processes; refusing launch. Allow the process check or use the existing visible browser.') from exc


def profile_browser_pids(profile, process_text):
    """Identify the official main browser, excluding helpers and other apps."""
    matching = set(active_profile_pids(profile, process_text))
    result = []
    for line in process_text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, command = int(parts[0]), parts[1]
        if (pid in matching and command.startswith(str(CHROME) + ' --')
                and not re.search(r'(?:^|\s)--type(?:=|\s)', command)):
            result.append(pid)
    return result


def request_profile_close(report):
    """With explicit CLI opt-in, ask only this dedicated browser to exit."""
    if report.get('profile_kind') != 'connectrobo_persistent':
        raise StartupError('Automatic close is allowed only for a dedicated ConnectRobo profile. A shared profile must be closed manually.')
    profile = report['user_data_dir']
    pids = profile_browser_pids(profile, read_processes())
    if len(pids) != 1:
        raise StartupError('Cannot identify one exact ConnectRobo Chrome browser process. Close that Chrome instance manually; no close signal sent.')
    pid = pids[0]
    # Recheck identity immediately before signaling; never signal helper groups
    # or use application-wide commands that could close JobRobo's browser.
    if profile_browser_pids(profile, read_processes()) != [pid]:
        raise StartupError('ConnectRobo Chrome process changed during inspection. Retry; no close signal sent.')
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass  # It exited between the identity check and the close request.
    except OSError as exc:
        raise StartupError('Cannot request ConnectRobo Chrome to close. Close that Chrome instance manually and retry.') from exc
    return pid


def stale_profile_lock(profile):
    """Prove an orphaned local singleton without modifying any profile files."""
    lock = Path(profile) / 'SingletonLock'
    channel = Path(profile) / 'SingletonSocket'
    try:
        target = os.readlink(lock)
        hostname, separator, value = target.rpartition('-')
        if not separator or hostname != socket.gethostname() or not value.isdecimal() or int(value) <= 1:
            return False
        owner = int(value)
        try:
            os.kill(owner, 0)  # Existence probe only; no signal is delivered.
        except ProcessLookupError:
            pass
        else:
            return False
        endpoint = os.readlink(channel) if channel.is_symlink() else None
        if endpoint is None and channel.exists():
            return False
        if endpoint is not None:
            path = Path(endpoint)
            if not path.is_absolute():
                path = channel.parent / path
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(.25)
                try:
                    client.connect(str(path))
                except OSError as error:
                    if error.errno not in (errno.ENOENT, errno.ECONNREFUSED):
                        return False
                else:
                    return False  # A listener means the profile is not idle.
        # Refuse recovery if Chrome replaced either marker during inspection.
        if os.readlink(lock) != target:
            return False
        if endpoint is None:
            if channel.exists() or channel.is_symlink():
                return False
        elif not channel.is_symlink() or os.readlink(channel) != endpoint:
            return False
        try:
            os.kill(owner, 0)
        except ProcessLookupError:
            return True
    except (OSError, ValueError, OverflowError):
        pass
    return False


def check_ready(config_path):
    try:
        base = preflight(config_path)
        config = load_config(config_path)
    except (OSError, ValueError, KeyError, SyntaxError, TypeError) as exc:
        raise StartupError('Invalid configuration or profile/source mismatch; run chrome_preflight.py for details.') from exc
    if config.get('session_strategy') != 'open_if_idle_else_handoff':
        raise StartupError('session_strategy must be open_if_idle_else_handoff')
    action = config.get('busy_profile_action', 'private_fallback')
    if action not in ('private_fallback', 'handoff'):
        raise StartupError('busy_profile_action must be private_fallback or handoff')
    if base['profile_kind'] == 'connectrobo_persistent':
        action = 'handoff'
    if config.get('chrome_binary') != str(CHROME) or not CHROME.is_file() or not os.access(CHROME, os.X_OK):
        raise StartupError('Official Google Chrome executable missing or misconfigured; install Chrome from Google or correct chrome_binary.')
    profile = Path(base['user_data_dir'])
    args = [str(CHROME), '--new-window', '--no-first-run',
            '--user-data-dir=' + str(profile)]
    directory = config.get('profile_directory')
    if directory is not None:
        if not isinstance(directory, str) or not directory or Path(directory).name != directory or directory in ('.', '..'):
            raise StartupError('profile_directory must be one existing subfolder name')
        if not (profile / directory).is_dir():
            raise StartupError('Configured profile_directory does not exist; refusing to create another profile')
        args.append('--profile-directory=' + directory)
    args.append(URL)
    try:
        pids = active_profile_pids(profile, read_processes())
        state = 'running' if pids else ('locked_without_matching_process' if base['lock_markers'] else 'idle')
        if (state == 'locked_without_matching_process'
                and base['profile_kind'] == 'connectrobo_persistent' and stale_profile_lock(profile)):
            state = 'stale_lock'
    except StartupError:
        pids, state = [], 'unknown'
    outcomes = {
        'idle': ('PREFLIGHT_PASS', 'The configured profile is idle; normal startup may open Chrome.'),
        'stale_lock': ('PROFILE_RECOVERABLE', 'The local Chrome lock owner is gone and no singleton socket is listening. Startup can reopen the same saved profile and let Chrome recover its stale locks; preflight changes no profile files.'),
        'running': ('BROWSER_ALREADY_OPEN', 'Use the existing exact-profile Chrome window. This CLI does not attach or focus it; automatic browser control and LinkedIn login remain unverified.'),
        'locked_without_matching_process': ('PROFILE_LOCKED', 'Lock markers exist without a matching process. They may be stale or belong to a session not visible to inspection; inspect Chrome manually. No locks removed and no new instance launched.'),
        'unknown': ('PROFILE_STATE_UNKNOWN', 'Process inspection failed. Existing-browser state is unknown; allow the read-only process check or inspect the existing Chrome window. No new instance launched.'),
    }
    result, next_step = outcomes[state]
    if state == 'running' and base['profile_kind'] == 'connectrobo_persistent':
        next_step = 'Sending runs wait for you to close this ConnectRobo Chrome instance, or use --restart-browser to close it automatically and reuse the saved profile. Use --open-only to open LinkedIn without sending. Preflight opens no tab.'
    launchable = state in ('idle', 'stale_lock')
    return {'mode': 'read_only_no_send', 'configuration_ready': True,
            'profile_state': state, 'profile_idle': None if state == 'unknown' else launchable,
            'browser_open': True if state == 'running' else (False if launchable else None),
            'can_launch_new_instance': launchable,
            'stale_lock_recovery': state == 'stale_lock',
            'automation_ready': False, 'attachment_supported': False,
            'fallback_proposed': state == 'running' and action == 'private_fallback',
            'fallback_mode': 'temporary_private' if action == 'private_fallback' else None,
            'profile_kind': base['profile_kind'],
            'create_profile_if_missing': base['create_profile_if_missing'],
            'lock_markers': base['lock_markers'], 'matching_process_ids': pids,
            'result': result, 'next': next_step, 'user_data_dir': str(profile),
            'browser_launched': False, 'login_verified': False,
            'outreach_actions': 0, 'launch_arguments': args}


@contextmanager
def startup_lease(root=ROOT):
    # Serialize this launcher's starts without modifying the browser profile.
    with (Path(root) / '.chrome-start.lock').open('a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StartupError('Another ConnectRobo startup is checking this profile; retry after it finishes.') from exc
        yield


def prepare_profile(report):
    """Create only an explicitly configured persistent profile at launch time."""
    if report.get('create_profile_if_missing'):
        Path(report['user_data_dir']).mkdir(mode=0o700, parents=True, exist_ok=True)


def reopen_profile(report):
    """Forward a URL through Chrome's singleton to the running saved profile.

    Chrome's short-lived command client exits after notifying the existing
    browser. This does not attach an automation controller or verify login.
    """
    arguments = [arg for arg in report['launch_arguments'] if arg != '--new-window']
    try:
        child = subprocess.Popen(arguments, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True, close_fds=True)
        code = child.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        # If Chrome closed during the check, this may now be a real browser.
        # Leave it alone instead of killing it as subprocess.run(timeout) would.
        raise StartupError('Chrome has not acknowledged the open request yet; inspect the ConnectRobo window. No process was killed.') from exc
    except OSError as exc:
        raise StartupError('Cannot send the open request to the saved Chrome profile; check Chrome and retry.') from exc
    if code != 0:
        raise StartupError('Chrome rejected the open request (code %s); inspect the ConnectRobo window.' % code)
    report.update(result='CHROME_PROFILE_REUSED', browser_reused=True,
                  launch_arguments=arguments,
                  next='Chrome accepted the request to open LinkedIn using the saved ConnectRobo profile. Sign in only if prompted. Login and automation are not verified by this launcher.')
    return report


def start(config_path, open_browser=False, login_observer=None):
    with startup_lease(ROOT):
        report = check_ready(config_path)
        if not open_browser:
            if report['fallback_proposed']:
                report['next'] = 'Normal startup will open a separate temporary ConnectRobo private session and request manual LinkedIn login. Preflight creates no fallback profile or browser; login/automation remain unverified.'
            return report
        if sys.platform != 'darwin':
            raise StartupError('This launcher is configured for macOS only')
        # Recheck immediately before launch; Chrome's own profile singleton is
        # the final cross-application arbiter. Never remove its lock files.
        report = check_ready(config_path)
        if report['profile_state'] == 'running' and report.get('profile_kind') == 'connectrobo_persistent':
            return reopen_profile(report)
        if report['fallback_proposed']:
            try:
                report = prepare_private_session(report, ROOT, CHROME, read_processes, active_profile_pids)
            except (OSError, ValueError) as exc:
                raise StartupError('Cannot safely prepare a private fallback profile; no browser launched.') from exc
        if not report['can_launch_new_instance']:
            if report.get('profile_kind') == 'temporary_private_fallback':
                report.update(observe_login(login_observer))
            return report
        try:
            prepare_profile(report)
            child = subprocess.Popen(report['launch_arguments'], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True, close_fds=True)
        except OSError as exc:
            raise StartupError('Chrome failed to start. Check the installed Chrome executable; no login or outreach attempted.') from exc
        # Detect immediate startup failure without waiting on a long-lived browser.
        time.sleep(0.5)
        code = child.poll()
        if code is not None:
            raise StartupError('Chrome launcher exited immediately (code %s); visible startup is unverified. Inspect the existing browser; no process was killed.' % code)
        report.update(browser_launched=True, browser_pid=child.pid, profile_state='starting',
                      profile_idle=False, browser_open=True, can_launch_new_instance=False,
                      result='CHROME_PROCESS_STARTED',
                      next='Verify profile path and LinkedIn login in the visible window; process start alone does not verify login or automation readiness.')
        if report.get('profile_kind') == 'temporary_private_fallback':
            report.update(observe_login(login_observer))
            if report['login_state'] == 'observation_required':
                report['login_state'] = 'login_needed'
            report['result'] = 'PRIVATE_BROWSER_READ_ONLY_READY' if report['login_verified'] else 'PRIVATE_BROWSER_LOGIN_HANDOFF'
            report['next'] = ('Continue observation-only readiness; no invitation/follow/message actions.' if report['login_verified'] else
                              'Sign in manually in the new ConnectRobo private Chrome window at linkedin.com/login, complete MFA/CAPTCHA yourself, then ask the agent to verify via supported browser control and continue read-only readiness. Do not save passwords. The stock CLI has no login observer and does not automatically resume or claim login.')
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'chrome_session.json')
    parser.add_argument('--open', action='store_true', help='Open LinkedIn in the saved Chrome profile, reusing its running session when available')
    args = parser.parse_args(argv)
    try:
        report = start(args.config, args.open)
        print(json.dumps(report, indent=2))
        if report['profile_state'] in ('locked_without_matching_process', 'unknown'):
            return 2
    except StartupError as exc:
        print(json.dumps({'result': 'BLOCKED', 'error': str(exc), 'login_verified': False, 'outreach_actions': 0}), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({'result': 'BLOCKED', 'error': 'Cannot access the startup lease or configuration; check local file permissions.', 'login_verified': False, 'outreach_actions': 0}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
