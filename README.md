# ConnectRobo

ConnectRobo runs one bounded batch of LinkedIn connection requests using its own saved Chrome profile. The current run configuration allows up to **50 attempts**, searching up to **20 pages / 200 candidates**. Change those limits and search terms in [`run_config.json`](run_config.json) before starting.

The normal command **sends connection requests**. Use `--preflight` to inspect startup readiness or `--open-only` to open LinkedIn without outreach. Sending supports no-note invitations only; it does not follow people, send messages, write notes, or enter recipient email addresses.

## Contents

- [Setup and first run](#setup-and-first-run)
- [Run configuration](#run-configuration)
- [Saved Chrome profile configuration](#saved-chrome-profile-configuration)
- [Command-line options and examples](#command-line-options-and-examples)
- [Connection behavior and attempt accounting](#connection-behavior-and-attempt-accounting)
- [Reports, logs, and screenshots](#reports-logs-and-screenshots)
- [Troubleshooting and current limits](#troubleshooting-and-current-limits)
- [Browser-only startup and legacy private fallback](#browser-only-startup-and-legacy-private-fallback)
- [Tests and project files](#tests-and-project-files)
- [Legacy script and attribution](#legacy-script-and-attribution)

## Setup and first run

Use macOS, Python 3.11 or later, and official Google Chrome installed at `/Applications/Google Chrome.app`. Selenium is required for sending; the browser-only launcher and offline checks use Python's standard library. Initial Selenium startup may download a compatible driver through Selenium Manager.

From the repository directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-live.txt
```

Review `run_config.json` before running a batch. For an optional machine-specific browser configuration, copy the example **only if your local file does not already exist**:

```sh
if [ ! -f chrome_session.json ]; then
  cp chrome_session.example.json chrome_session.json
fi
```

Check readiness and open the saved profile without sending:

```sh
.venv/bin/python connectrobo.py --preflight
.venv/bin/python connectrobo.py --open-only
```

Sign in manually in Chrome if prompted. Complete any authentication challenges yourself. Close this ConnectRobo Chrome instance before sending, or use the restart option below. JobRobo and other Chrome profiles can remain open.

Run one sending batch:

```sh
.venv/bin/python connectrobo.py
```

If ConnectRobo Chrome is already open, automatically close that exact browser instance and reopen the same saved profile for the batch:

```sh
.venv/bin/python connectrobo.py --restart-browser
```

The default command waits for a busy profile to close instead of attaching to the existing browser. Its wait comes from `profile_timeout_seconds`. If another run is already waiting, cancel that run with Ctrl+C before starting another.

The owned Selenium browser closes when a batch finishes or stops. Chrome keeps the profile on disk, including its saved login; LinkedIn can still ask you to sign in again when the session expires. This browser closure is normal cleanup, not evidence of a crash.

## Run configuration

The CLI reads [`run_config.json`](run_config.json) beside the script on every sending run, before opening Chrome. There is no daily allowance or recurring schedule: each normal invocation creates a new run ID and attempt allowance. Previously attempted recipients remain excluded across runs.

Example matching the current configuration:

```json
{
  "keywords": "founder bay area",
  "network": ["S"],
  "max_attempts": 50,
  "max_pages": 20,
  "max_candidates": 200,
  "login_timeout_seconds": 180,
  "profile_timeout_seconds": 180
}
```

| Setting | Purpose and accepted values |
| --- | --- |
| `keywords` | Nonempty people-search text. Words such as `bay area` are search terms; no separate geographic filter is added. |
| `network` | Nonempty list of unique LinkedIn filter values: `F`, `S`, or `O`. Current setting: `["S"]`. |
| `max_attempts` | Positive integer. Maximum durable invitation reservations in this run, including email-required skips, failed attempts, and uncertain attempts. |
| `max_pages` | Positive integer. Maximum search pages visited; pagination can end earlier. |
| `max_candidates` | Positive integer. Maximum identified search-result entries reviewed, including duplicates and other skipped candidates. |
| `login_timeout_seconds` | Integer from 1 to 600. Time allowed for manual login and account navigation readiness. |
| `profile_timeout_seconds` | Integer from 0 to 600. Time allowed for the dedicated Chrome profile to become available. Zero fails immediately if busy; restart requires a positive wait. |

All seven keys are required. Unknown keys, malformed JSON, missing files, and invalid values stop sending startup. The three attempt/search limits are independent; the program does not silently increase one to satisfy another.

Use a different file with `--run-config /path/to/run-settings.json`. Explicit `--login-timeout` and `--profile-timeout` flags override the wait values in that file. Search filters remain fixed and are checked against the active page throughout the run.

The supported CLI uses JSON configuration; `.env` is not loaded for these settings. Do not put LinkedIn passwords or recipient email addresses in configuration files. The program uses manual sign-in and the saved Chrome session.

## Saved Chrome profile configuration

Browser settings are separate from outreach/search settings. By default, the CLI looks for local `chrome_session.json`; if absent, the committed [`chrome_session.example.json`](chrome_session.example.json) supplies the dedicated profile configuration. `--config` selects another browser settings file.

The default user-data directory is:

```text
~/Library/Application Support/ConnectRobo/ChromeProfile
```

This is separate from JobRobo's Chrome profile. Startup can create a missing dedicated ConnectRobo directory; preflight only inspects and never creates it. To verify the selected subprofile, open `chrome://version` in a browser-only window and inspect **Profile Path**.

| Setting | Purpose |
| --- | --- |
| `user_data_dir` | Chrome user-data root. `~` expands to your home directory. Use ConnectRobo's dedicated directory. |
| `profile_kind` | `connectrobo_persistent` for the reusable dedicated profile. Legacy shared profiles have different startup restrictions. |
| `create_profile_if_missing` | `true` permits creation at startup for the dedicated persistent profile only. |
| `profile_directory` | Optional existing subfolder such as `Default`; `null` lets Chrome select normally. |
| `profile_source` | Keep `null` for the dedicated profile. Legacy shared configurations can reference another application's literal profile setting. |
| `profile_source_key` | Legacy source setting name, normally `chrome_profile_path`; read without executing the source file. |
| `chrome_binary` | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. |
| `busy_profile_action` | Use `handoff`. Browser-only mode can reuse the running persistent profile; sending waits for it to close. Legacy shared profiles may use `private_fallback`. |
| `mode` | Keep `read_only_no_send`. This is a browser-launcher guard, not the sending mode selector. Plain `connectrobo.py` still sends. |
| `session_strategy` | Keep `open_if_idle_else_handoff`. |
| `browser`, `max_pages` | Keep `chrome` and `1` in the browser config. Live search depth is controlled separately by `run_config.json`. |
| `credentials`, `allowed_mutations`, `launch_second_instance` | Keep `null`, `[]`, and `false`. |

If a local browser config still points at JobRobo, change `user_data_dir` to the dedicated ConnectRobo path, set `profile_kind` to `connectrobo_persistent`, `create_profile_if_missing` to `true`, `profile_source` to `null`, and `busy_profile_action` to `handoff`.

`--restart-browser` sends one graceful shutdown request (`SIGTERM`) only to the verified main Chrome process using the dedicated ConnectRobo profile. It waits for release, then opens Selenium with that same profile. It does not target JobRobo or unrelated Chrome instances, sign out, force-kill Chrome, or delete lock files. Shared profiles cannot be closed through this option.

## Command-line options and examples

```sh
.venv/bin/python connectrobo.py --help
```

| Option | Behavior |
| --- | --- |
| No mode flag | Send one configured batch using Selenium and a fresh run ID. |
| `--preflight` | Inspect browser configuration/profile state without opening Chrome or sending. Does not load run settings or verify login. |
| `--open-only` | Open LinkedIn with the saved profile without outreach. Does not independently verify login. |
| `--connect-once` | Explicit alias for the default sending batch. |
| `--config PATH` | Browser/profile JSON file. Defaults to `chrome_session.json` beside the script, with the example fallback described above. |
| `--run-config PATH` | Sending/search JSON file. Defaults to `run_config.json` beside the script. |
| `--run-id ID` | Nonempty receipt identity. Reuse only to continue the same allowance; an unresolved prior reservation blocks continuation. |
| `--controller selenium` | Explicitly select the controller already used by default. |
| `--login-timeout SECONDS` | Override the configured login wait; 1–600 seconds. |
| `--profile-timeout SECONDS` | Override the configured profile wait; 0–600 seconds. |
| `--restart-browser` | Gracefully close the exact dedicated ConnectRobo Chrome instance before a sending run. Requires a positive profile wait. |
| `-h`, `--help` | Show usage without starting a run. |

`--preflight`, `--open-only`, and `--connect-once` are mutually exclusive. `--controller`, `--run-id`, and `--restart-browser` are sending-only options and cannot accompany either browser-only mode. Run settings and timeout overrides govern sending; browser-only commands do not use those settings.

Choose a separate run configuration:

```sh
.venv/bin/python connectrobo.py --run-config /path/to/run-settings.json
```

Use custom browser settings for a preflight check:

```sh
.venv/bin/python connectrobo.py --config /path/to/chrome-settings.json --preflight
```

Restart the dedicated browser and give login/profile release more time:

```sh
.venv/bin/python connectrobo.py --restart-browser --login-timeout 300 --profile-timeout 300
```

Choose an explicit run identity:

```sh
.venv/bin/python connectrobo.py --connect-once --controller selenium --run-id my-batch-001
```

Save both terminal streams to a local file. Choose a new filename for each run so previous output is retained:

```sh
mkdir -p logs
.venv/bin/python -u connectrobo.py > logs/my-batch-output.log 2>&1
```

The direct browser-only equivalent of `--open-only` remains available:

```sh
.venv/bin/python chrome_start.py --open
```

## Connection behavior and attempt accounting

Before outreach, the controller waits for account navigation, opens `/in/me/`, and independently verifies the signed-in account's profile URL and full name. A feed URL alone does not establish authentication.

The batch searches the configured keywords/network, captures each results page before profile navigation, and reviews candidates within the page/candidate bounds. It verifies the exact recipient URL, full name, and Connect state again on the profile before reserving an attempt and acting.

| Candidate or outcome | Behavior | Uses an attempt slot? |
| --- | --- | --- |
| Previously attempted recipient, duplicate result, or self | Skip without Connect/Send. | No |
| Pending, connected, or Follow/Message-only result | Skip without action. | No |
| Unrecognized controls in an otherwise ready results page | Skip without action. | No |
| No-note invitation | Reserve durably before Connect; click Connect once and Send once if required. Verify success independently. | Yes |
| Verified email-required invitation | Close the dialog without entering an email or clicking Send; record `skipped_email_required`; continue. | Yes |
| Known `not_sent` outcome | Record a failed attempt and continue if the page remains safe. | Yes |
| Submission or receipt uncertainty | Record uncertain and stop; never automatically retry the recipient. | Yes |
| Recipient name, URL, or state differs between search and profile | Stop before reservation and submission. | No |
| Checkpoint, restriction, unexpected origin, or unresolved control error | Stop. | Any reservation already made remains consumed. |

An entirely unready search page is retried only within the read deadline, then stops if still unrecognized. Current Pending links are recognized read-only, so a page of pending requests can advance to the next search page. Hiring/opportunity links alongside names are excluded from profile-name selection; ambiguous canonical name links are not used.

A successful receipt requires Pending on the same profile, a fresh recipient-specific **Invitation sent** notice after Send, or a matching pending row in Sent invitations after Send. The exact profile URL and full heading remain verified. A notice may address complete leading name words, including compound given names, or an explicitly displayed parenthetical nickname. Preexisting notices, partial words, surname-only text, and other recipients cannot confirm a send. Success notices are rechecked across nested safety reads; other populated warnings still stop the run.

For an email-required skip, the adapter requires the observed email-verification text, the same exact profile/full name, one empty text/email input, and a disabled Send control. It dismisses one uniquely identified close button and verifies that the dialog is gone and the profile still shows Connect. An ambiguous form, changed recipient, failed dismissal, or other input request still stops. It never types an email or note. This skip counts toward both `attempted_or_reserved` and `skipped`, but not `failed` or `uncertain`.

The limit is an **attempt allowance**, not a promise of that many delivered requests. For example, 50 slots with three email-required skips permit at most 47 confirmed sends. Failed/uncertain attempts also consume slots. Ordinary skips consume only the candidate review allowance. A batch can finish earlier because results end or a stop condition occurs.

Before Connect, the engine appends and flushes an attempt reservation to the receipt journal. Reservations protect against duplicate actions after crashes or control loss. Recipients with reservations remain excluded in future runs, including known email-required skips, failures, and uncertain outcomes. Keep the journal; deleting it removes that protection. Resuming the same explicit run ID preserves its existing allowance and refuses to resume unresolved reservations.

## Reports, logs, and screenshots

Progress and verification diagnostics go to stderr. The final JSON report goes to stdout and includes the run ID, search scope, configured bounds, account identity, result, and receipt counts. Counts overlap for email-required skips as described above.

| Field | Meaning |
| --- | --- |
| `attempted_or_reserved` | Unique recipients reserved in this run, including email-required skips and unresolved reservations. |
| `confirmed` | Recipients with independently confirmed invitation receipts. |
| `failed` | Known `not_sent` outcomes. |
| `uncertain` | Reservations with unknown submission/receipt outcome. |
| `skipped` | Ordinary skip events plus verified email-required skip outcomes. |
| `candidates_reviewed` | Search-result entries examined in this invocation; a stopped candidate can be included before any journal event is written for it. |
| `limit`, `max_pages`, `max_candidates` | The active sending and search bounds. |
| `verification` | Stage, route category, current safety state, selector counts, and optional generated failure screenshot metadata. |
| `failure_class`, `failure_location` | Sanitized exception class and code locations, when an exception caused the stop. |

An abbreviated cap-reached report can look like this:

```json
{
  "result": "ATTEMPT_CAP_REACHED",
  "limit": 50,
  "max_pages": 20,
  "max_candidates": 200,
  "candidates_reviewed": 120,
  "attempted_or_reserved": 50,
  "confirmed": 47,
  "failed": 0,
  "uncertain": 0,
  "skipped": 73
}
```

Here 70 ordinary skips and three email-required skips produce 73 skips; the email-required recipients also consume three of the 50 attempt slots.

| Local artifact | Purpose |
| --- | --- |
| `logs/invitation-receipts.jsonl` | Append-only receipt journal, locked during a run. Contains recipient names/URLs, run/account identity, timestamps, outcomes, and reasons. Keep for deduplication. |
| `logs/<your-output-file>.log` | Optional console capture created by your shell redirection; not created automatically by the CLI. |
| `evidence/submission-failure-<id>.png` | Best-effort owner-readable screenshot after a submission exception. The final `verification` reports its filename and `failure_screenshot_saved` flag. |
| `.selenium-cache/` | Selenium Manager's local driver cache. |

These artifacts are local and ignored by git. Screenshots may contain profile information. A screenshot failure does not replace the original exception. An engine-level stop before submission, such as an identity mismatch, does not currently create a submission-failure screenshot. Verification diagnostics omit credentials, cookies, and page text; the final report and receipt journal include account/recipient identity information.

Common sending results:

| Result | Meaning and next step |
| --- | --- |
| `ATTEMPT_CAP_REACHED` | The configured attempt allowance is consumed. |
| `REVIEW_BOUND_REACHED` | The candidate review boundary was reached. |
| `CANDIDATES_EXHAUSTED` | The configured page search ended or available results ended before another limit. |
| `AUTH_OR_CONTROL_UNVERIFIED` | Account authentication or browser control could not be independently verified. Check the visible login/navigation state. |
| `STOPPED_PROFILE_UNAVAILABLE` | The saved profile remained busy, locked, or uninspectable. Close the dedicated browser or inspect the lock condition. |
| `STOPPED_IDENTITY_OR_STATE_CHANGED` | Search result and profile verification differ. The recipient was not submitted. |
| `STOPPED_UNCERTAIN_SUBMISSION` | Submission/receipt is unverified. Inspect evidence; do not retry that recipient automatically. |
| `STOPPED_UNCERTAIN_PRIOR_ATTEMPT` | The explicit run ID contains an unresolved reservation. Review it before further work. |
| `STOPPED_CHECKPOINT_OR_WARNING` | A checkpoint or populated warning requires inspection. |
| `STOPPED_CONTROL_OR_SELECTOR_ERROR` | A selector, observation deadline, WebDriver operation, or profile read failed. Inspect the exception locations. |
| `STOPPED_UNEXPECTED_SEARCH_SCOPE`, `STOPPED_UNVERIFIED_IDENTITY`, `STOPPED_ACCOUNT_CHANGED` | The search or identity guard failed; inspect the mismatch before continuing. |
| `STOPPED_ADAPTER_STARTUP_OR_LOGIN`, `STOPPED_RUN_ERROR` | Startup/login or outer run error; inspect sanitized details and any `next` instruction. |

Sending commands return exit 2 for `STOPPED_*`, `AUTH_*`, or `CONTROLLER_*` reports; normal cap/search completion returns 0. Invalid command-line/config settings produce a usage error before the browser starts.

## Troubleshooting and current limits

| Symptom | Explanation and action |
| --- | --- |
| Browser opens, then closes | A sending run closes its owned browser during final cleanup, including on stops. Read the final JSON `result`; the saved login remains. Use `--open-only` for a browser-only session. |
| Waiting for Chrome to close | Another process owns the dedicated profile. Close that instance or cancel the waiting command and rerun with `--restart-browser`. JobRobo can stay open. |
| `PROFILE_LOCKED` / unknown process state | Startup cannot prove the profile is available. Inspect the browser/process state; do not manually remove singleton locks. |
| Zero sends after many skips | Prior attempts, Pending/Follow/Message results, and unknown cards can consume the review boundary. Adjust keywords/network or the independent page/candidate limits. |
| `STOPPED_IDENTITY_OR_STATE_CHANGED` repeats at the same candidate | Names or canonical URLs differ. For example, search `Rishabh Singh` versus profile `Rishabh (Rey) Singh` still fails the exact comparison. Nickname support in success notices does not relax this earlier identity check. The current implementation stops the batch rather than skipping this mismatch. |
| Search `ObservationTimeout` | Inspect whether results or supported action controls rendered. Known plain Pending links and hiring-link name ambiguity are handled; wholly unsupported layouts still stop within the read deadline. |
| Recipient requires email | A verified email-only requirement is dismissed, counted as `skipped_email_required`, and the batch continues. Ambiguous variants still stop and preserve the reservation. |
| `ModuleNotFoundError: selenium` | Install the live dependency with `.venv/bin/python -m pip install -r requirements-live.txt`. |
| WebDriver error while reading another profile | The run stops rather than guessing. Inspect exception locations and verify Chrome/driver startup before another sending run. |
| A macOS/Python/Chrome update changed behavior | Run offline tests, preflight, and browser-only startup before sending. The repository does not certify every OS/browser combination. |

Only visible English controls and the observed legacy/current layouts are supported. The adapter can inspect the normal document and the open shadow root of `#interop-outlet` for invitation dialogs. It does not traverse More menus, bypass challenges, or supply recipient emails. Read-only observation retries tolerate supported stale-element/time-out cases within the original deadline; navigation and Connect/Send actions are never replayed by those retries. There is no account-safety guarantee or promise that LinkedIn will permit the configured number of requests.

## Browser-only startup and legacy private fallback

Preflight separates configuration readiness, profile availability, and automation/login verification. A successful preflight does not establish an authenticated account or live sending compatibility.

| Browser-only result | Meaning | Exit |
| --- | --- | --- |
| `PREFLIGHT_PASS` | Valid configuration; profile idle; startup may launch Chrome. | 0 |
| `PROFILE_RECOVERABLE` | A proven stale local lock can be recovered by Chrome using the same saved profile. | 0 |
| `BROWSER_ALREADY_OPEN` | Exact-profile browser already running; preflight opens no tab. | 0 |
| `CHROME_PROFILE_REUSED` | `--open-only` forwarded LinkedIn to the running saved profile; login remains unverified. | 0 |
| `CHROME_PROCESS_STARTED` | Browser process started; page/login still need observation. | 0 |
| `PROFILE_LOCKED`, `PROFILE_STATE_UNKNOWN` | Availability cannot be established; launch is blocked. | 2 |
| `BLOCKED` | Invalid configuration, missing browser/profile, or another startup error. | 2 |
| `PRIVATE_BROWSER_LOGIN_HANDOFF` | Legacy temporary private browser started; manual login required. | 0 |
| `PRIVATE_BROWSER_ALREADY_OPEN` | Existing legacy fallback detected; no second launch. | 0 |
| `PRIVATE_PROFILE_LOCKED` | Legacy fallback lock/process state is unverified; launch blocked. | 2 |

Preflight can accept a not-yet-created dedicated profile without creating it. For a busy saved profile, `--open-only` uses Chrome's existing-session URL handling; `browser_launched: false` and `can_launch_new_instance: false` are expected. These modes report `login_verified: false`, `automation_ready: false`, and `attachment_supported: false`; the launcher does not attach Selenium to a running ordinary Chrome instance.

For stale locks, startup checks the local host, missing owner PID, and absence of a singleton socket listener, then lets Chrome perform its own recovery. Foreign owners, live processes, listeners, and failed inspection keep startup blocked. It never directly deletes Chrome locks or copies authentication databases. A local lease serializes starts from this checkout; Chrome's singleton lock guards the profile itself.

### Optional legacy private fallback

Older shared-profile browser-only configurations can set `busy_profile_action: private_fallback`. This is disabled for `connectrobo_persistent` and is not used by sending runs. It allocates a temporary `connectrobo-session-*` directory and launches Chrome incognito, without copying JobRobo cookies or credentials. Metadata is stored in ignored `.connectrobo-fallback.json`.

An existing running fallback is reused for handoff; an unverified fallback lock blocks launch. After the old fallback becomes idle, a later invocation allocates another private session. Login is not retained across private sessions. Temporary directories are not deleted while in use. On `PRIVATE_BROWSER_LOGIN_HANDOFF`, sign in manually and complete challenges yourself; its optional `login_observer` supports non-sending observation only.

## Tests and project files

Run offline checks from the repository directory:

```sh
.venv/bin/python -m unittest discover -s . -v
.venv/bin/python safe_sample.py
.venv/bin/python connectrobo.py --preflight
```

Tests mock browser/network actions and use synthetic identities. `safe_sample.py` reads a fixed local HTML fixture with no browser, credentials, requests, or actions. `safe_config.json` belongs to that sample and does not configure live search depth. Preflight inspects local configuration/processes without opening Chrome. Offline checks do not establish live LinkedIn compatibility.

After an OS, Python, or browser change, follow those checks with a non-sending browser-only check:

```sh
.venv/bin/python connectrobo.py --open-only
```

| File | Role |
| --- | --- |
| `connectrobo.py` | Supported CLI entry point. |
| `run_config.json`, `run_config.py` | Search/bounds/wait settings and validation. |
| `chrome_session.example.json`, local `chrome_session.json` | Browser executable and persistent profile settings. |
| `connection_run.py` | Attempt accounting, deduplication, receipt journal, and batch stop rules. |
| `selenium_controller.py` | Account/profile/search observation, invitation UI, email skips, and receipt verification. |
| `chrome_start.py`, `chrome_preflight.py` | Browser-only startup, profile inspection, and dedicated-profile restart/recovery. |
| `fallback_session.py` | Optional legacy private browser-only fallback. |
| `requirements-live.txt` | Selenium dependency for the supported live workflow. |
| `safe_sample.py`, `safe_config.json`, `fixture.html` | Separate offline observation example. |
| `test_*.py` | Offline regression coverage for CLI, profiles, guards, search, sending outcomes, and skips. |

Back up the full working directory and the saved Chrome profile before a machine migration or OS upgrade. Git ignores local browser config, `.env` files, logs, evidence, virtual environments, caches, and browser data; a git commit alone does not preserve them. Keep credentials and profile databases out of commits.

## Legacy script and attribution

`Linkedin_auto_connector_bot.py` is the historical automatic outreach script. It contains embedded login configuration and can send connections/follows without the supported guards. Do not run or import it for this workflow. Its bundled driver, requirements, and message template are not used by the supported `connectrobo.py` launcher. Personalized messages and follows remain unsupported.

MIT License; original copyright (c) 2024 Pavlo Bondarenko. See [LICENSE](LICENSE). The original project README attributed [OfficialCodeVoyage/LinkedIn_Auto_Connector_Bot](https://github.com/OfficialCodeVoyage/LinkedIn_Auto_Connector_Bot). Repository: [dannyb66/connectrobo](https://github.com/dannyb66/connectrobo).
