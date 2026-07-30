# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python script (`checker.py`) that polls two Icelandic tour-booking systems for seat
availability on a specific date (August 18, 2026, min 2 people) for three Jökulsárlón glacier
lagoon boat tours, and sends a WhatsApp notification via Twilio the moment seats open up. It runs
for free on a GitHub Actions cron schedule — there is no server, database, or persistent process.

## Running it

```bash
pip install -r requirements.txt

export TWILIO_ACCOUNT_SID="ACxxxxxxxx"
export TWILIO_AUTH_TOKEN="xxxxxxxx"
export TWILIO_FROM="whatsapp:+14155238886"
export WA_TO="whatsapp:+39XXXXXXXXXX"

python checker.py
```

- Set `SIMULATE_AVAILABLE=1` to force every tour to report "available" without hitting the real
  booking APIs — useful for testing the Twilio/WhatsApp send path. Comment out `send_whatsapp(...)`
  in `run_checks()` if you want to simulate without actually sending a message.
- The four `TWILIO_*` / `WA_TO` env vars are read via `os.environ[...]` (not `.get()`), so the
  script hard-fails immediately at import time if any is missing.
- No test suite, linter, or build step exists in this repo — there's nothing to run beyond the
  script itself.

## Architecture

Everything lives in `checker.py`. There is no browser automation despite what README.md's
"Come funziona" section says (that description is stale) — the script makes **direct HTTP
requests** to each booking system's internal API, no Playwright/browser involved.

The three monitored tours use two different booking backends, each with its own check function:

- **`check_liva(tour)`** — for `icelagoon.is` tours (Zodiac, Amphibian), which use the **LIVA**
  booking system. A single GET to `explore.liva.is/.../calendar-availability` returns a list of
  days for the month; the function scans for `2026-08-18` and checks `available` + `availableSlots
  >= MIN_PEOPLE`.
- **`check_bokun(tour)`** — for `icelagoon.com` (Adventure Tour), which uses **Bokun**. A POST to
  `widgets.bokun.io/widgets/{seller}/activity/{product}/2026/8` with a pricing-category/quantity
  body returns a calendar of weeks/days; the function finds Aug 18 by matching an epoch-ms
  timestamp, then checks `activityAvailabilityStatus == "AVAILABLE"` and scans `availabilities`
  slots for one with `availabilityCount >= MIN_PEOPLE`.

Each tour's config dict in the `TOURS` list carries whichever backend-specific fields it needs
(`liva_tag` vs. `bokun_seller`/`bokun_product`/`bokun_cat_id`) alongside a `system` discriminator
used to dispatch to the right check function in `run_checks()`.

**State and dedup.** `state.json` (checked into the repo, committed by CI after every run) tracks
`notified` (tour names already alerted — never re-notify the same tour once it's fired),
`last_check`, and a rolling `errors` counter. Once a tour name appears in `notified`, `run_checks()`
skips checking it entirely for the rest of the script's life (including across separate cron runs).

**Internal polling loop.** `main()` doesn't just run one check and exit — it loops up to 11 times
(`LOOP_DURATION_MIN=55` / `LOOP_INTERVAL_MIN=5`), sleeping 5 minutes between iterations, calling
`save_state()` after each. This keeps the process running near-continuously while staying under
GitHub Actions' job time limits, without needing a separate scheduler. The loop exits early once
all tours are in `notified`.

**Error handling.** Each tour's check is wrapped in its own try/except inside `run_checks()` — one
tour's exception doesn't stop the others from being checked. Consecutive errors increment
`state["errors"]`; a success resets it to 0. Every 6th consecutive error triggers a "check GitHub"
WhatsApp warning (best-effort — failures to send that warning are swallowed).

## CI / deployment (`.github/workflows/check.yml`)

- Runs hourly (`0 * * * *`) plus manual `workflow_dispatch`, `timeout-minutes: 58` (the internal
  Python loop covers the other 55 minutes of watching, so effectively one job = ~55 min of
  checking).
- Checks out the repo (so it has the current `state.json`), installs `requirements.txt`, runs
  `python checker.py` with the four Twilio secrets from repo Settings → Secrets, then commits
  `state.json` back with `[skip ci]` if it changed and pushes — this is how notification state
  persists between runs without an external database.
- If you change how/when state gets saved in `checker.py`, keep in mind CI commits whatever
  `state.json` looks like on disk at the end of the job — `save_state()` must actually run for that
  to have effect.

## Conventions to preserve

- Keep the whole thing dependency-light: only `requests` is required (see `requirements.txt`).
  Don't reintroduce Playwright/browser automation unless a booking system genuinely requires
  JS rendering again — the direct-API approach is what both check functions rely on now.
- New tours are added by appending a dict to `TOURS` with `system: "liva"` or `system: "bokun"` and
  that backend's required fields; `run_checks()` dispatches purely on the `system` key.
- Print statements (not logging) are the existing convention for progress/debug output, since
  GitHub Actions job logs are the only place this output is read.
- `README.md` is in Italian and end-user/setup-focused; its "Come funziona" diagram describing
  Playwright is out of date relative to `checker.py`'s actual direct-HTTP implementation — prefer
  the code as source of truth, and consider fixing that section if you touch the README.
