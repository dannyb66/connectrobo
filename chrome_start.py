"""Browser-only startup for ConnectRobo. No login, invitation or messaging code."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from chrome_preflight import load_config, preflight

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


def check_ready(config_path):
    try:
        base = preflight(config_path)
        config = load_config(config_path)
    except (OSError, ValueError, KeyError, SyntaxError, TypeError) as exc:
        raise StartupError('Invalid configuration or profile/source mismatch; run chrome_preflight.py for details.') from exc
    if config.get('session_strategy') != 'open_if_idle_else_handoff':
        raise StartupError('session_strategy must be open_if_idle_else_handoff')
    if config.get('chrome_binary') != str(CHROME) or not CHROME.is_file() or not os.access(CHROME, os.X_OK):
        raise StartupError('Official Google Chrome executable missing or misconfigured; install Chrome from Google or correct chrome_binary.')
    profile = Path(base['user_data_dir'])
    pids = active_profile_pids(profile, read_processes())
    if base['lock_markers'] or pids:
        raise StartupError('PROFILE_BUSY: use the existing profile window, or close that profile normally before retrying. No locks removed and no processes killed.')
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
    return {'mode': 'read_only_no_send', 'configuration_ready': True,
            'profile_idle': True, 'user_data_dir': str(profile),
            'browser_launched': False, 'login_verified': False,
            'outreach_actions': 0, 'launch_arguments': args}


def start(config_path, open_browser=False):
    # Serialize this launcher's starts without modifying the browser profile.
    with (ROOT / '.chrome-start.lock').open('a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise StartupError('Another ConnectRobo startup is checking this profile; retry after it finishes.') from exc
        report = check_ready(config_path)
        if not open_browser:
            report['result'] = 'PREFLIGHT_PASS: browser not launched'
            return report
        if sys.platform != 'darwin':
            raise StartupError('This launcher is configured for macOS only')
        # Recheck immediately before launch; Chrome's own profile singleton is
        # the final cross-application arbiter. Never remove its lock files.
        report = check_ready(config_path)
        try:
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
        report.update(browser_launched=True, browser_pid=child.pid,
                      result='CHROME_PROCESS_STARTED: verify profile path and LinkedIn login in the visible window')
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'chrome_session.json')
    parser.add_argument('--open', action='store_true', help='Start visible official Chrome if the shared profile is idle; opens LinkedIn feed only')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(start(args.config, args.open), indent=2))
    except StartupError as exc:
        print(json.dumps({'result': 'BLOCKED', 'error': str(exc), 'login_verified': False, 'outreach_actions': 0}), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({'result': 'BLOCKED', 'error': 'Cannot access the startup lease or configuration; check local file permissions.', 'login_verified': False, 'outreach_actions': 0}), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
