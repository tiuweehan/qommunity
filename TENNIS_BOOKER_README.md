# Qommunity Facility Booker

This documents `tennis_booker.py`, the CLI used to list facilities, query availability, book slots, cancel bookings, and run a long-lived scheduler.

## Files

```text
tennis_booker.py                 CLI script
booking_base_config.json         shared scheduler defaults
facilities.json                  facility keys and IDs
sunday_8am_bookings_10y.json     actual booking requests
qommunity_auth.json              saved OTP login response and Bearer token, mode 600
auth_config.json                 local ignored OTP login contact config
.env                             local ignored cron/login environment
.env.example                     template for VPS environment
tennis_booker.log                default stdout/stderr log file
```

After CLI login, the script uses the saved Bearer token from:

```text
qommunity_auth.json
```

If that file does not exist, the script falls back to the latest captured Bearer token from:

```text
/tmp/qommunity-ipmitm-flows.mitm
```

Run commands with:

```bash
~/venv/bin/python tennis_booker.py ...
```

## Output And Logging

By default, stdout/stderr are shown in the terminal and mirrored to:

```text
tennis_booker.log
```

Terminal output is colored. The log file is plain text with ANSI color codes stripped.

Options:

```bash
--log-file tennis_booker.log
--log-file ""          # disable file logging
--no-color             # disable terminal color
```

The CLI logs:

```text
Countdown timing
Poll attempt start
Request sent timestamp
Response status, elapsed_ms, byte count
Availability summary
Validation start/end
Booking confirmation start/end
Cancellation/status fetches
Token expiry and token reloads
```

## CLI Reference

```text
--config PATH
  Base scheduler config. Usually booking_base_config.json.

--facilities-config PATH
  Facility definitions. Usually facilities.json.

--bookings-config PATH
  Booking request list. Usually sunday_8am_bookings_10y.json.

--list-facilities
  Fetch available facilities from the API and print local key, ID, name, and category.

--write-facilities-config PATH
  Fetch available facilities and write a normalized facilities[] JSON config.

--log-file PATH
  Mirror stdout/stderr to a file. Default: tennis_booker.log. Use "" to disable.

--no-color
  Disable ANSI colors in terminal output. Logs are always written without ANSI codes.

--date YYYY-MM-DD
  One-off availability/booking date.

--flow-file PATH
  mitmproxy flow file used to extract the latest Bearer token. Default: /tmp/qommunity-ipmitm-flows.mitm.

--auth-file PATH
  Saved auth JSON. Default: qommunity_auth.json. Used before --flow-file when present.

--auth-config PATH
  Auth config JSON. Defaults to auth_config.json, then --config or booking_base_config.json.

--login [email|mobile]
  Send an OTP to the configured contact, prompt for the OTP, exchange it for a Bearer token,
  save --auth-file with mode 600, then exit. `--login` without a value means email.

--auth-contact VALUE
  Login contact. Can also be set with QOMMUNITY_AUTH_CONTACT or auth.email.contact/auth.mobile.contact
  in auth_config.json.

--auth-mobile-country-code VALUE
  Login mobile country code. Used only for mobile login. Default for mobile mode: +65.

--auth-contact-type VALUE
  Qommunity login contactType. Defaults to 2 for email, 1 for mobile.

--auth-client-id VALUE
  Qommunity client_id. Defaults to the captured app client ID.

--auth-accept-tc / --no-auth-accept-tc
  Whether to send TCAccepted=true during token generation. Default: true.

--otp VALUE
  OTP value for --login. If omitted, the CLI prompts interactively.

--otp-source prompt|worker
  OTP source for --login. Defaults to otp.source in auth_config.json, else prompt.

--otp-worker-url URL
  Secret-protected Cloudflare Worker /otp endpoint used by --otp-source worker.

--otp-secret-file PATH
  File containing the Worker read secret. Prefer this over --otp-secret.

--otp-timeout-seconds N
  Seconds to wait for Worker OTP. Default: 180.

--token TOKEN
  Use a fixed Bearer token instead of qommunity_auth.json or the flow file.

--token-refresh-skew SECONDS
  Reload captured token this many seconds before expiry. Default: 300. Use 0 to disable.

--facility-id UUID
  One-off mode facility ID. Default is Tennis Court 3.

--payment-method METHOD
  Payment method for confirmation. Default: EstateCredit.

--preferred-start HH:MM:SS
  One-off preferred start time. Repeat to add fallbacks in priority order.

--validate
  Send validation POST when a slot is available.

--book
  Actually confirm bookings. Without this, scheduler config stays dry-run unless book=true in config.

--notify-due-tonight
  In config mode, send a Telegram summary of jobs whose probe start is later today, then exit.
  This is used by the 08:00 daily reminder cron. It checks Qommunity availability and only
  reports jobs whose booking date is the earliest date currently marked Not Yet Open.

--job-index N
  In config mode, run only the Nth pending job after sorting. 0-based. Use this for cron sharding.
  For example, two cron invocations with --job-index 0 and --job-index 1 can book two independent
  same-night jobs without either process handling both.

--show-booking-id UUID
  Fetch and print a booking by ID.

--cancel-booking-id UUID
  Cancel a booking by ID, then fetch it again to confirm status.

--cancel-reason TEXT
  Cancellation reason. Default: Wrong timing.

--watch
  Keep polling in one-off mode until booked/found or max attempts is reached.

--interval SECONDS
  Poll interval for one-off watch mode. Scheduler uses config interval.

--max-attempts N
  Max attempts for one-off watch mode. 0 means unlimited.

--wait-until ISO_TIMESTAMP
  Wait until a timestamp before starting one-off mode.
```

## Config Files

For VPS cron, copy `.env.example` to `.env`, fill in the real values, and keep `.env` mode `600`.
`tennis_booker.py` auto-loads `.env` from the current working directory before parsing CLI flags.

Telegram notifications are optional and controlled by `.env`:

```text
QOMMUNITY_TELEGRAM_TOKEN
QOMMUNITY_TELEGRAM_DEBUG_CHAT_ID=-1004406554510
QOMMUNITY_TELEGRAM_BOOKING_CHAT_ID=-1004417606652
```

Notification behavior:

```text
OTP login started/succeeded/failed -> Tennis Debug
Booking cron started -> Tennis Debug
08:00 due-tonight summary -> Tennis Booking
Due booking about to run -> Tennis Booking
Booking succeeded/failed after polling -> Tennis Booking
```

`booking_base_config.json` contains reusable defaults:

```json
{
  "defaults": {
    "advance_days": 30,
    "open_time": "00:00:00",
    "lead_seconds": 1,
    "interval": 0.2,
    "max_attempts": 900,
    "payment_method": "EstateCredit",
    "validate": true,
    "book": false
  }
}
```

`facilities.json` maps stable local keys to API facility IDs:

```json
{
  "facilities": [
    {
      "key": "tennis_court_3",
      "name": "Tennis Court 3",
      "facility_id": "55868819-d547-4ac5-be87-6882310b90de",
      "category": "a"
    }
  ]
}
```

`sunday_8am_bookings_10y.json` contains actual booking requests:

```json
{
  "bookings": [
    {
      "facility": "tennis_court_3",
      "date": "2026-07-26",
      "preferred_starts": ["08:00:00"]
    }
  ]
}
```

`preferred_starts` is ordered. The script chooses the first available timing in the list.

## Cron One-Shot Scheduler

Dry run:

```bash
~/projects/qommunity/.venv/bin/python tennis_booker.py \
  --config booking_base_config.json \
  --facilities-config facilities.json \
  --bookings-config sunday_8am_bookings_10y.json \
  --due-window-seconds 120
```

Live booking:

```bash
~/projects/qommunity/.venv/bin/python tennis_booker.py \
  --config booking_base_config.json \
  --facilities-config facilities.json \
  --bookings-config sunday_8am_bookings_10y.json \
  --due-window-seconds 120 \
  --book
```

Scheduling behavior:

```text
The scheduler first checks Qommunity availability for each facility.
A config job is due only if its booking date is the earliest date currently marked Not Yet Open.
For a due job, open_at is treated as the next configured open_time, and start_at is open_at minus lead_seconds.
```

With current defaults, if Qommunity says `2026-07-26` is the earliest Not Yet Open date,
the script treats it as opening at:

```text
the next 00:00:00 +08:00
```

The script starts probing at:

```text
one second before that, e.g. 23:59:59 +08:00
```

Cron behavior:

```text
Run once per day at 23:59 SGT.
Only jobs matching the earliest Not Yet Open date and whose start_at is within --due-window-seconds are considered.
The script may sleep briefly until start_at, then polls until booked or max_attempts is reached.
It exits after the due jobs are handled.
```

`advance_days` remains in the config as a fallback/sort hint for pure local scheduling helpers, but live cron uses the API's earliest `Not Yet Open` date instead of trusting a hard-coded release window.

## Facility Listing

The facility list endpoint is:

```text
GET /api/v1.0/portfolio/{propertyId}/unit/{unitId}/facility
```

Print facilities:

```bash
~/venv/bin/python tennis_booker.py --list-facilities
```

Refresh `facilities.json`:

```bash
~/venv/bin/python tennis_booker.py --write-facilities-config facilities.json
```

Current known useful key:

```text
tennis_court_3  55868819-d547-4ac5-be87-6882310b90de
```

## One-Off Availability Query

Check Tennis Court 3 for a date/time without booking:

```bash
~/venv/bin/python tennis_booker.py \
  --date 2026-07-26 \
  --preferred-start 08:00:00
```

Watch repeatedly:

```bash
~/venv/bin/python tennis_booker.py \
  --date 2026-07-26 \
  --preferred-start 08:00:00 \
  --watch \
  --interval 0.2
```

## One-Off Booking

Book the first available preferred start:

```bash
~/venv/bin/python tennis_booker.py \
  --date 2026-07-26 \
  --preferred-start 08:00:00 \
  --book
```

Multiple fallback timings:

```bash
~/venv/bin/python tennis_booker.py \
  --date 2026-07-26 \
  --preferred-start 08:00:00 \
  --preferred-start 07:00:00 \
  --book
```

## Auth Login

Generate a fresh auth file without MITM. Email login is the default:

```bash
~/venv/bin/python tennis_booker.py --login
~/venv/bin/python tennis_booker.py --login email
~/venv/bin/python tennis_booker.py --login mobile
```

By default, `--login` reads contact settings from:

```text
auth_config.json
```

That file is ignored by git. Shape:

```json
{
  "auth": {
    "client_id": "fbc7149c8b3244ddb754c090918b7621.mtwpublicapp.com.ibase",
    "email": {
      "contactType": "2",
      "contact": "you@example.com"
    },
    "mobile": {
      "contactType": "1",
      "contact": "81234567",
      "mobileCountryCode": "+65"
    }
  }
}
```

Override the config path if needed:

```bash
~/venv/bin/python tennis_booker.py --login --auth-config ~/.qommunity_auth_config.json
```

CLI flags override config values:

```bash
~/venv/bin/python tennis_booker.py --login email --auth-contact you@example.com
~/venv/bin/python tennis_booker.py --login mobile --auth-contact 81234567 --auth-mobile-country-code +65
```

The CLI calls:

```text
POST /api/v1.0/auth/requesttoken
POST /api/v1.0/auth/generatetoken
```

For automated email OTP, see `OTP_AUTOMATION.md`.

It prompts for the OTP, then writes:

```text
qommunity_auth.json
```

The file contains the API login response, including `loginOTPOutput.accessToken` and
`loginOTPOutput.refreshToken`, and is written with `600` permissions. Treat it as a secret.

You can override the destination:

```bash
~/venv/bin/python tennis_booker.py --login --auth-file ~/.qommunity_auth.json
```

All normal booking commands automatically use `--auth-file` when it exists. If no auth file
exists, they fall back to `--flow-file`.

## Booking Status And Cancellation

Fetch a booking:

```bash
~/venv/bin/python tennis_booker.py \
  --show-booking-id <booking-id>
```

Cancel a booking:

```bash
~/venv/bin/python tennis_booker.py \
  --cancel-booking-id <booking-id> \
  --cancel-reason "Wrong timing"
```

The cancel command posts to:

```text
POST /api/v1.0/portfolio/{propertyId}/unit/{unitId}/booking/{bookingId}/cancel
```

Then it fetches the booking again and exits `0` only if status is `Cancelled`.

## Token Handling

The script cannot forge or extend JWT expiry. It uses legitimate tokens from either CLI OTP login
or the MITM flow file.

Token behavior:

```text
If --token is passed, that token is fixed.
Otherwise, if --auth-file exists, the script uses loginOTPOutput.accessToken from that file.
Otherwise, the script extracts the latest token from the flow file.
During long runs using the flow file, it reloads the token before expiry.
If the API returns 401 while using the flow file, it reloads once and retries the request.
```

Refresh auth with `--login`. MITM is now only needed as a fallback.

## Exit Codes

```text
0  Success, dry-run slot found, booking confirmed, cancellation confirmed, or facility command completed
1  One-shot poll did not find an available slot, or cancellation did not end as Cancelled
2  Watch reached max attempts
```

## Tests

Run the local scheduler/config tests with:

```bash
~/venv/bin/python -m unittest discover -s tests -v
```

The tests cover due-window selection, 08:00 due-tonight reminder selection,
`--job-index` sharding, past-window skipping, deterministic ordering, and the
10-year Sunday config containing separate `07:00` and `08:00` entries rather
than fallback lists.

## Current Live Booking Cron Command

```bash
~/projects/qommunity/.venv/bin/python tennis_booker.py \
  --config booking_base_config.json \
  --facilities-config facilities.json \
  --bookings-config sunday_8am_bookings_10y.json \
  --due-window-seconds 120 \
  --book \
  --log-file tennis_booker.log
```
