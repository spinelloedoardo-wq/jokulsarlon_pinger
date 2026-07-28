"""
Jökulsárlón Boat Tours — availability checker for August 18.

Booking systems:
  icelagoon.is  → LIVA (explore.liva.is) — direct API, no browser needed
  icelagoon.com → Bokun (widgets.bokun.io) — Playwright (JS-rendered widget)

Required env vars:
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM / WA_TO
Optional:
  SIMULATE_AVAILABLE=1  → skip real checks, force "available" for testing
"""

import os, json, time, random, sys
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM        = os.environ["TWILIO_FROM"]
WA_TO              = os.environ["WA_TO"]
SIMULATE           = os.environ.get("SIMULATE_AVAILABLE", "0") == "1"

TARGET_LABEL = "18 agosto 2026"
TARGET_DATE  = "2026-08-18"

TOURS = [
    {
        "name":    "Zodiac Boat Tour (icelagoon.is)",
        "url":     "https://icelagoon.is/tours/zodiac-boat-tour/",
        "system":  "liva",
        "liva_tag": "fun-adventure",
    },
    {
        "name":    "Amphibian Tour (icelagoon.is)",
        "url":     "https://icelagoon.is/tours/amphibian-tours/",
        "system":  "liva",
        "liva_tag": "adventure-tour",
    },
    {
        "name":    "Adventure Tour (icelagoon.com)",
        "url":     "https://www.icelagoon.com/adventure-tour/",
        "system":  "bokun",
        "bokun_channel": "50c3e856-1b67-4450-8dbb-aa6d293564f2",
        "bokun_product": "14059",
    },
]

STATE_FILE = "state.json"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ── State ─────────────────────────────────────────────────────────────────────

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"notified": [], "last_check": None, "errors": 0}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── LIVA check (icelagoon.is) — direct API call ───────────────────────────────

def check_liva(tour: dict) -> bool:
    """
    Calls the LIVA calendar-availability API for August 2026.
    Returns True if August 18 has available=True.
    """
    tag = tour["liva_tag"]
    url = (
        f"https://explore.liva.is/jokulsarlon/api/events/calendar-availability"
        f"?experienceTag={tag}&year=2026&month=8"
    )
    print(f"  LIVA API: {url}")
    resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    print(f"  Got {len(data)} days.")

    for day in data:
        start = day.get("startTime", "")
        if not start.startswith(TARGET_DATE):
            continue
        available     = day.get("available", False)
        avail_slots   = day.get("availableSlots", 0)
        print(f"  Aug 18: available={available}, slots={avail_slots}")
        return bool(available) and avail_slots >= 2

    print(f"  Aug 18 not found in response.")
    return False

# ── Bokun check (icelagoon.com) — Playwright + network intercept ──────────────

def check_bokun(tour: dict) -> bool:
    """
    Loads the Bokun booking iframe directly via Playwright, intercepts the
    calendar-availability JSON, and checks August 18.
    Returns True if available.
    """
    channel = tour["bokun_channel"]
    product = tour["bokun_product"]

    # Load the experience-calendar iframe directly (no host page needed)
    iframe_url = (
        f"https://widgets.bokun.io/online-sales/{channel}/experience-calendar/{product}"
    )

    captured: list[dict] = []

    def on_response(resp):
        try:
            if "bokun.io" not in resp.url:
                return
            if resp.status != 200:
                return
            ct = resp.headers.get("content-type", "")
            if "json" not in ct:
                return
            body = resp.json()
            if body:
                captured.append({"url": resp.url, "body": body})
                print(f"  [bokun JSON] {resp.url[:100]}")
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        ctx = browser.new_context(
            user_agent=UA, locale="en-GB",
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()
        page.on("response", on_response)
        page.route("**/*.{png,jpg,jpeg,gif,ico,woff,woff2,ttf,eot}", lambda r: r.abort())

        print(f"  Loading Bokun iframe: {iframe_url}")
        try:
            page.goto(iframe_url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"  Timeout (continuing): {e}")

        time.sleep(5)

        # Try to navigate to August 2026 in the calendar
        _bokun_navigate_to_august(page)

        time.sleep(4)
        browser.close()

    return _parse_bokun_responses(captured)


def _bokun_navigate_to_august(page) -> None:
    """Click 'next month' in the Bokun calendar until August 2026."""
    for _ in range(14):
        try:
            header = page.locator(
                ".CalendarMonth_caption strong, .DayPicker-Caption div, "
                "[class*='month'] [class*='caption'], h2"
            ).first
            text = header.inner_text(timeout=2000)
            print(f"  Calendar: {text.strip()}")
            if "August" in text and "2026" in text:
                return
        except Exception:
            pass

        try:
            btn = page.locator(
                "[aria-label='Move forward to switch to the next month'], "
                "[aria-label='Next month'], "
                ".DayPickerNavigation_rightButton__horizontalDefault, "
                "button[class*='next' i], button[class*='Next']"
            ).first
            btn.click(timeout=3000)
            time.sleep(1.5)
        except Exception as e:
            print(f"  Can't click next: {e}")
            return


def _parse_bokun_responses(captured: list[dict]) -> bool:
    if not captured:
        print("  No Bokun JSON captured — assuming not available.")
        return False

    for r in captured:
        body = r["body"]
        items = body if isinstance(body, list) else (
            body.get("days") or body.get("items") or body.get("availability") or []
        )
        for item in items:
            date = (
                item.get("date") or item.get("startDate") or
                item.get("localDate") or item.get("startTime", "")
            )
            if not str(date).startswith(TARGET_DATE):
                continue
            sold_out  = item.get("soldOut", False) or item.get("isSoldOut", False)
            available = item.get("available", True)
            status    = str(item.get("status", "")).upper()
            print(f"  Bokun Aug 18: soldOut={sold_out}, available={available}, status={status}")
            if sold_out or not available or status in ("SOLD_OUT", "UNAVAILABLE"):
                return False
            return True

    print("  Aug 18 not found in Bokun responses.")
    return False

# ── WhatsApp ──────────────────────────────────────────────────────────────────

def send_whatsapp(message: str) -> None:
    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"From": TWILIO_FROM, "To": WA_TO, "Body": message},
        timeout=60,
    )
    if not resp.ok:
        print(f"  Twilio error {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    print(f"  WhatsApp sent → {resp.status_code}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(timezone.utc).isoformat()
    print(f"[{now}] Checking Jökulsárlón for {TARGET_LABEL}...")

    state = load_state()
    notified: list[str] = state.get("notified", [])

    try:
        for tour in TOURS:
            name = tour["name"]
            if name in notified:
                print(f"\n[SKIP] {name} — already notified.")
                continue

            print(f"\n[CHECK] {name}")
            time.sleep(random.uniform(1, 3))

            try:
                if SIMULATE:
                    available = True
                    print("  [SIMULATE] available=True")
                elif tour["system"] == "liva":
                    available = check_liva(tour)
                else:
                    available = check_bokun(tour)
                state["errors"] = 0
            except Exception as e:
                state["errors"] = state.get("errors", 0) + 1
                print(f"  ERROR: {e}", file=sys.stderr)
                available = False

            if available:
                send_whatsapp(
                    f"JOKULSARLON — {TARGET_LABEL} DISPONIBILE!\n"
                    f"Tour: {name}\n"
                    f"Prenota subito: {tour['url']}"
                )
                notified.append(name)
            else:
                print("  Not available yet.")

        state["notified"] = notified
        state["last_check"] = now

        errors = state.get("errors", 0)
        if errors > 0 and errors % 6 == 0:
            try:
                send_whatsapp(f"Jokulsarlon Pinger: {errors} errori consecutivi. Controlla GitHub.")
            except Exception:
                pass

    finally:
        save_state(state)
        print("\nDone.")

if __name__ == "__main__":
    main()
