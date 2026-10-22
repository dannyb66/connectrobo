"""Run a configured batch of no-note connection attempts using the saved profile."""
import argparse
import json
from pathlib import Path
import sys
from uuid import uuid4
import chrome_start
import selenium_controller
from connection_run import run_connections, verification_diagnostic, exception_details
from run_config import load_run_config


def main(argv=None, controller=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('chrome_session.json'))
    parser.add_argument('--run-config', type=Path, default=Path(__file__).with_name('run_config.json'),
                        help='JSON file with search terms, run bounds and wait timeouts')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--preflight', action='store_true', help='Check only; do not launch Chrome')
    mode.add_argument('--open-only', action='store_true', help='Open LinkedIn without sending connection requests')
    mode.add_argument('--connect-once', action='store_true', help='Run one batch (the default behavior)')
    parser.add_argument('--run-id', help='Receipt identity; defaults to a fresh unique ID for each run')
    parser.add_argument('--controller', choices=['selenium'], help='Browser controller (default: selenium)')
    parser.add_argument('--login-timeout', type=int, help='Override configured manual login wait, 1–600 seconds')
    parser.add_argument('--profile-timeout', type=int, help='Override configured saved-profile wait, 0–600 seconds')
    parser.add_argument('--restart-browser', action='store_true', help='Close only ConnectRobo Chrome before the run, then reopen the same saved profile')
    args = parser.parse_args(argv)
    if (args.preflight or args.open_only) and (args.controller or args.run_id or args.restart_browser):
        parser.error('--controller, --run-id and --restart-browser are only available for sending runs')
    if args.login_timeout is not None and not 1 <= args.login_timeout <= 600:
        parser.error('--login-timeout must be between 1 and 600')
    if args.profile_timeout is not None and not 0 <= args.profile_timeout <= 600:
        parser.error('--profile-timeout must be between 0 and 600')
    if args.restart_browser and args.profile_timeout == 0:
        parser.error('--restart-browser requires --profile-timeout greater than 0')
    if args.run_id is not None and not args.run_id.strip():
        parser.error('--run-id must not be empty')
    if args.preflight or args.open_only:
        command = ['--config', str(args.config)]
        if args.open_only:
            command.append('--open')
        return chrome_start.main(command)

    try:
        settings = load_run_config(args.run_config)
    except (OSError, ValueError) as error:
        # Report configuration errors before creating a controller or receipt.
        parser.error('Cannot load run config %s: %s' % (args.run_config, error))
    login_timeout = settings['login_timeout_seconds'] if args.login_timeout is None else args.login_timeout
    profile_timeout = settings['profile_timeout_seconds'] if args.profile_timeout is None else args.profile_timeout
    if args.restart_browser and profile_timeout == 0:
        parser.error('--restart-browser requires a profile wait greater than 0')
    run_id = args.run_id or uuid4().hex
    owned = None
    stage = 'startup'
    print('Starting run %s: up to %s connection attempts; search up to %s pages / %s candidates using the saved Chrome profile.' %
          (run_id, settings['max_attempts'], settings['max_pages'], settings['max_candidates']), file=sys.stderr, flush=True)
    try:
        if controller is None:
            owned = selenium_controller.create_controller(Path(__file__).parent, args.config,
                                                          profile_timeout=profile_timeout,
                                                          restart_browser=args.restart_browser)
            controller = owned
            print('Sign in manually only if Chrome asks; subsequent runs reuse the login.', file=sys.stderr, flush=True)
            selenium_controller.wait_for_manual_login(controller, login_timeout)
        stage = 'run'
        report = run_connections(controller, Path(__file__).parent / 'logs/invitation-receipts.jsonl',
                                 run_id, limit=settings['max_attempts'], max_pages=settings['max_pages'],
                                 max_candidates=settings['max_candidates'], keywords=settings['keywords'],
                                 network=settings['network'])
    except Exception as error:
        # Never expose browser/session details from exception strings.
        report = dict(result='STOPPED_ADAPTER_STARTUP_OR_LOGIN' if stage == 'startup' else 'STOPPED_RUN_ERROR',
                      run_id=run_id, limit=settings['max_attempts'], max_pages=settings['max_pages'],
                      max_candidates=settings['max_candidates'], **exception_details(error),
                      verification=verification_diagnostic(controller),
                      **{key: 0 if stage == 'startup' else None for key in
                         ('attempted_or_reserved', 'confirmed', 'failed', 'uncertain', 'skipped')})
        if isinstance(error, selenium_controller.ProfileUnavailable):
            report.update(result='STOPPED_PROFILE_UNAVAILABLE', next=error.next_step)
        elif isinstance(error, ModuleNotFoundError) and error.name == 'selenium':
            report['next'] = 'Install the browser dependency: .venv/bin/python -m pip install -r requirements-live.txt'
    finally:
        if owned is not None:
            owned.driver.quit()
    print(json.dumps(report, indent=2))
    return 2 if report['result'].startswith(('CONTROLLER_', 'AUTH_', 'STOPPED_')) else 0


if __name__ == '__main__':
    raise SystemExit(main())
