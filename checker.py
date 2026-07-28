"""
Jökulsárlón Boat Tours — availability checker for August 18.

Monitors three tour pages (all on Bokun booking system) and alerts via
WhatsApp (Twilio) as soon as August 18 opens up.

Required env vars:
  TWILIO_ACCOUNT_SID  – Twilio Account SID (starts with AC...)
  TWILIO_AUTH_TOKEN   – Twilio Auth Token
  TWILIO_FROM         – Twilio WhatsApp number, e.g. whatsapp:+14155238886
  WA_TO               – Your WhatsApp number, e.g. whatsapp:+393407480234

Optional env vars:
  SIMULATE_AVAILABLE  – Set to "1" to force an availability notification (testing)
"""

import os
import json
import time
import random
import sys
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM        = os.environ["TWILIO_FROM"]
WA_TO              = os.environ["WA_TO"]

SIMULATE_AVAILABLE = os.environ.get("SIMULATE_AVAILABLE", "0") == "1"

TARGET_DATE_LABEL = "18 agosto 2026"
TARGET_YEAR  = 2026
TARGET_MONTH = 8   # August
TARGET_DAY   = 18

TOURS = [
    {
        "name": "Zodiac Boat Tour (icelagoon.is)",
        "url":  "https://icelagoon.is/tours/zodiac-boat-tour/",
    },
    {
        "name": "Amphibian Tour (icelagoon.is)",
        "url":  "https://icelagoon.is/tours/amphibian-tours/",
    },
    {
        "name": "Adventure Tour (icelagoon.com)",
        "url":  "https://www.icelagoon.com/adventure-tour/",
    },
]

STATE_FILE = "state.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "notified": [],      # list of tour names already notified as available
        "last_check": None,
        "errors": 0,
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── Bokun availability check via Playwright ───────────────────────────────────

def check_tour_availability(tour: dict) -> bool:
    """
    Opens the tour page with Playwright, intercepts Bokun API calls,
    and checks whether August 18 appears as available (not sold out).

    Returns True if the date is available for booking.
    """
    url  = tour["url"]
    name = tour["name"]

    bokun_responses: list[dict] = []

    def on_response(response):
        """Capture all Bokun API JSON responses."""
        try:
            if "bokun.io" in response.url and response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    bokun_responses.append({"url": response.url, "body": response.json()})
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        context = browser.new_context(
            user_agent=UA,
            locale="en-GB",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        # Intercept Bokun API responses
        page.on("response", on_response)

        # Block images/fonts to speed up load
        page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,ico}", lambda r: r.abort())

        print(f"  Loading {url} ...")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  Page load error: {e}")
            browser.close()
            return False

        # Wait for Bokun widget iframe to appear and load
        print("  Waiting for Bokun widget...")
        try:
            page.wait_for_selector("iframe[src*='bokun']", timeout=20000)
        except Exception:
            print("  Bokun iframe not found within 20s — checking what we have anyway.")

        # Give the widget time to make API calls for the current month
        time.sleep(random.uniform(5, 8))

        # If we're not in August yet, try to navigate the calendar forward
        # Look inside iframes for calendar navigation
        try:
            for frame in page.frames:
                if "bokun" in frame.url:
                    print(f"  Found Bokun iframe: {frame.url}")
                    # Try to find and click "next month" until we reach August 2026
                    _navigate_to_august(frame)
                    time.sleep(3)
                    break
        except Exception as e:
            print(f"  Frame navigation error: {e}")

        browser.close()

    # ── Parse captured API responses ──────────────────────────────────────────
    return _parse_bokun_responses(bokun_responses, name)


def _navigate_to_august(frame) -> None:
    """Try to click 'next month' arrows in the Bokun calendar frame until August 2026."""
    max_clicks = 14  # up to 14 months forward
    for _ in range(max_clicks):
        try:
            # Check current month shown in the calendar header
            header = frame.locator(".CalendarMonth_caption, .DayPicker-Caption, h2, .month-header").first
            header_text = header.inner_text(timeout=2000)
            print(f"    Calendar header: {header_text}")
            if "August" in header_text and "2026" in header_text:
                print("    Reached August 2026.")
                return
        except Exception:
            pass

        # Click next-month button
        try:
            next_btn = frame.locator(
                "[aria-label='Next month'], [aria-label='next month'], "
                ".DayPickerNavigation_button:last-child, "
                ".fc-next-button, button[class*='next'], "
                "button[class*='Next'], .navigation-next"
            ).first
            next_btn.click(timeout=3000)
            time.sleep(1.2)
        except Exception as e:
            print(f"    Could not click next: {e}")
            return


def _parse_bokun_responses(responses: list[dict], tour_name: str) -> bool:
    """
    Parse Bokun API JSON responses to determine if August 18 is available.
    Bokun typically returns an array of availability objects with date + available/soldOut fields.
    """
    if not responses:
        print(f"  No Bokun API responses captured for {tour_name}.")
        return False

    print(f"  Captured {len(responses)} Bokun API response(s).")

    for r in responses:
        url  = r["url"]
        body = r["body"]
        print(f"    API: {url}")

        # Bokun availability endpoint returns a list of day availability objects
        items = body if isinstance(body, list) else body.get("items", body.get("availability", []))
        if not isinstance(items, list):
            continue

        for item in items:
            # Bokun date fields: "date", "startDate", "localDate"
            date_str = (
                item.get("date")
                or item.get("startDate")
                or item.get("localDate")
                or item.get("startTime", "")
            )
            if not date_str:
                continue

            # Parse: "2026-08-18", "2026-08-18T00:00:00", "18.08.2026", ...
            is_target = _is_target_date(date_str)
            if not is_target:
                continue

            sold_out  = item.get("soldOut", False) or item.get("isSoldOut", False)
            available = item.get("available", True)
            unavailable = item.get("unavailable", False)
            closed    = item.get("closed", False)

            if sold_out or unavailable or closed or not available:
                print(f"    Aug 18 → SOLD OUT / unavailable on {tour_name}")
                return False
            else:
                print(f"    Aug 18 → AVAILABLE on {tour_name}!")
                return True

    print(f"  Aug 18 not found in API responses for {tour_name} — may be outside loaded months.")
    return False


def _is_target_date(date_str: str) -> bool:
    """Check if a date string refers to 2026-08-18."""
    s = str(date_str)
    # ISO format: 2026-08-18 or 2026-08-18T...
    if s.startswith("2026-08-18"):
        return True
    # DD.MM.YYYY
    if s.startswith("18.08.2026"):
        return True
    # MM/DD/YYYY
    if s.startswith("08/18/2026"):
        return True
    # Epoch ms — skip (too complex without context)
    return False

# ── WhatsApp notification ─────────────────────────────────────────────────────

def send_whatsapp(message: str) -> None:
    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/"
        f"{TWILIO_ACCOUNT_SID}/Messages.json"
    )
    resp = requests.post(
        url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": TWILIO_FROM, "To": WA_TO, "Body": message},
        timeout=60,
    )
    if not resp.ok:
        print(f"  Twilio error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    print(f"  WhatsApp sent → HTTP {resp.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Checking Jökulsárlón boat tours for {TARGET_DATE_LABEL}...")

    state = load_state()
    already_notified: list[str] = state.get("notified", [])

    try:
        for tour in TOURS:
            name = tour["name"]

            if name in already_notified:
                print(f"\n[SKIP] {name} — already notified.")
                continue

            print(f"\n[CHECK] {name}")

            if SIMULATE_AVAILABLE:
                available = True
                print("  [SIMULATE] Forcing available=True")
            else:
                time.sleep(random.uniform(2, 5))
                try:
                    available = check_tour_availability(tour)
                except Exception as e:
                    state["errors"] = state.get("errors", 0) + 1
                    print(f"  Error checking {name}: {e}", file=sys.stderr)
                    available = False

            if available:
                send_whatsapp(
                    f"JOKULSARLON — {TARGET_DATE_LABEL} DISPONIBILE!\n"
                    f"Tour: {name}\n"
                    f"Prenota subito: {tour['url']}"
                )
                already_notified.append(name)
                state["errors"] = 0
            else:
                print(f"  Not available yet.")

        state["notified"] = already_notified
        state["last_check"] = now

        # Alert if too many consecutive errors
        errors = state.get("errors", 0)
        if errors > 0 and errors % 6 == 0:
            try:
                send_whatsapp(
                    f"Jokulsarlon Pinger: {errors} errori consecutivi. "
                    "Controlla il workflow su GitHub."
                )
            except Exception:
                pass

    finally:
        save_state(state)
        print("\nState saved. Done.")


if __name__ == "__main__":
    main()
