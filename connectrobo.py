"""Normal ConnectRobo startup: reopen its saved Chrome profile, never outreach."""
import argparse
from pathlib import Path
import chrome_start


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('chrome_session.json'))
    parser.add_argument('--preflight', action='store_true', help='Check only; do not launch Chrome')
    args = parser.parse_args(argv)
    command = ['--config', str(args.config)]
    if not args.preflight:
        command.append('--open')
    return chrome_start.main(command)


if __name__ == '__main__':
    raise SystemExit(main())
