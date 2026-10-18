# ConnectRobo

Open Chrome with an existing profile and inspect LinkedIn without sending outreach. The supported `connectrobo.py` command reuses the configured user-data directory and opens the LinkedIn feed. It does not log in, send invitations, follow people or send messages.

## Run on macOS

Python 3.11+ and the official Google Chrome application are recommended. The safe startup and fixture commands use only Python's standard library; Selenium is not needed for them.

```sh
cd /path/to/connectrobo
python3 -m venv .venv
.venv/bin/python connectrobo.py --preflight
.venv/bin/python connectrobo.py
```

The normal command automatically starts visible Chrome if the configured profile is idle. A process-start result does not confirm LinkedIn login. If login is needed, sign in directly in Chrome and complete MFA yourself. Do not put credentials in configuration files or chat.

If the profile is already running or has a lock marker, startup returns `PROFILE_BUSY`: use the existing window or close that profile normally before retrying. The launcher does not kill processes, delete locks or start another instance against a busy profile. It refuses to launch if process inspection fails. A local lease serializes starts from this checkout; Chrome's own singleton lock is the final guard against races with other applications. Do not launch JobRobo and ConnectRobo against the same profile simultaneously.

## Saved profile configuration

Without a local override, the committed `chrome_session.example.json` supplies the default existing profile `~/Library/Application Support/JobRobo/ChromeProfile`. The launcher refuses to create a replacement profile if that directory is missing.

For machine-specific settings:

```sh
cp chrome_session.example.json chrome_session.json
```

Edit the ignored `chrome_session.json`; future runs automatically read it. Keep this file local. The example contains no credentials, and the program never reads or copies cookie/password databases.

| Setting | Purpose |
|---|---|
| `user_data_dir` | Existing Chrome user-data root; `~` expands to your home folder. |
| `profile_directory` | Optional existing subfolder such as `Default`; `null` lets Chrome select normally. |
| `profile_source` | Optional path to JobRobo's `config/settings.py`; `null` requires no other checkout. If supplied, its literal profile setting must match. |
| `profile_source_key` | JobRobo setting name, normally `chrome_profile_path`; parsed without executing the file. |
| `chrome_binary` | Official installed Chrome executable: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. |
| `mode` | Must remain `read_only_no_send`. |
| `session_strategy` | Must remain `open_if_idle_else_handoff`. |
| `credentials`, `allowed_mutations`, `launch_second_instance` | Must remain `null`, `[]`, `false`. |

Inspect `chrome://version` in the opened window to verify its Profile Path. The active subprofile should be under the configured user-data root. Selecting recipients or filters and granting permission for outreach are separate steps; this launcher includes no sending implementation.

Advanced commands:

```sh
.venv/bin/python connectrobo.py --config /path/to/local-config.json --preflight
.venv/bin/python chrome_start.py --open
.venv/bin/python safe_sample.py
.venv/bin/python -m unittest discover -s . -v
```

`--preflight` does not open a browser. Exit 0 means the requested check/start step succeeded; exit 2 returns a blocked/error result. Tests mock browser creation and make no external requests. The synthetic fixture demonstrates control counting only; it does not validate today's authenticated LinkedIn selectors.

## Legacy script and current limits

`Linkedin_auto_connector_bot.py` is the historical automatic outreach script. It contains embedded login configuration and sends connections/follows without a dry-run guard. **Do not run or import it for this workflow.** The supported startup is `connectrobo.py`. Embedded credentials and bundled drivers are never used by the supported launcher.

Live account identity, people-search filters, invitation success, personalized messages and automatic sending are not implemented or verified by these startup tests. The legacy template is unused, file logging claimed by its original documentation is absent, and its error/count handling is unreliable. No account-safety guarantee is implied by this project.

## Attribution and license

MIT License; original copyright (c) 2024 Pavlo Bondarenko. Retain [LICENSE](LICENSE). The original project README attributed [OfficialCodeVoyage/LinkedIn_Auto_Connector_Bot](https://github.com/OfficialCodeVoyage/LinkedIn_Auto_Connector_Bot). Repository: [dannyb66/connectrobo](https://github.com/dannyb66/connectrobo).

The safe launcher independently follows the existing JobRobo profile-setting convention (`chrome_profile_path` → `--user-data-dir`). It does not import or copy JobRobo's launcher, change its configuration, add stealth dependencies, create a debugging endpoint or extract authentication data.
