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

# icelagoon.com Bokun credentials (known from page source)
ICELAGOON_COM_CHANNEL_UUID = "50c3e856-1b67-4450-8dbb-aa6d2935644f"
ICELAGOON_COM_PRODUCT_ID   = "14059"

TOURS = [
    {
        "name": "Zodiac Boat Tour (icelagoon.is)",
        "url":  "https://icelagoon.is/tours/zodiac-boat-tour/",
        "bokun_channel": None,  # discovered dynamically via Playwright
        "bokun_product": None,
    },
    {
        "name": "Amphibian Tour (icelagoon.is)",
        "url":  "https://icelagoon.is/tours/amphibian-tours/",
        "bokun_channel": None,
        "bokun_product": None,
    },
    {
        "name": "Adventure Tour (icelagoon.com)",
        "url":  "https://www.icelagoon.com/adventure-tour/",
        "bokun_channel": ICELAGOON_COM_CHANNEL_UUID,
        "bokun_product": ICELAGOON_COM_PRODUCT_ID,
    },
]

STATE_FILE = "state.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BOKUN_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://widgets.bokun.io",
    "Referer": "https://widgets.bokun.io/",
}

# ── State ─────────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "notified": [],
        "last_check": None,
        "errors": 0,
    }


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── Direct Bokun API (for tours where we know channel+product) ────────────────

def check_via_bokun_api(channel_uuid: str, product_id: str, tour_name: str) -> bool | None:
    """
    Query Bokun's widget API directly for August 2026 availability.
    Returns True=available, False=sold out, None=inconclusive (try Playwright fallback).
    """
    # Bokun widget availability endpoint (reverse-engineered from widget XHR)
    endpoints = [
        f"https://widgets.bokun.io/online-sales/{channel_uuid}/experience/{product_id}/availability?month=2026-08",
        f"https://widgets.bokun.io/online-sales/{channel_uuid}/experience/{product_id}/calendar?year=2026&month=8",
        f"https://widgets.bokun.io/api/v1/sell/experience/{product_id}/availabilities?startDate=2026-08-01&endDate=2026-08-31",
    ]

    for endpoint in endpoints:
        try:
            print(f"  Trying API: {endpoint}")
            resp = requests.get(endpoint, headers=BOKUN_HEADERS, timeout=20)
            print(f"  → HTTP {resp.status_code}")
            if resp.status_code != 200:
                continue

            data = resp.json()
            print(f"  → Response keys: {list(data.keys()) if isinstance(data, dict) else 'list['+str(len(data))+']'}")

            result = _parse_bokun_response_data(data, tour_name)
            if result is not None:
                return result

        except Exception as e:
            print(f"  API error: {e}")

    return None  # inconclusive


def _parse_bokun_response_data(data, tour_name: str) -> bool | None:
    """Parse a Bokun API response. Returns True/False/None."""
    items = data if isinstance(data, list) else (
        data.get("days") or data.get("items") or data.get("availability") or
        data.get("availabilities") or data.get("dates") or []
    )

    if not isinstance(items, list) or not items:
        return None

    print(f"  Parsing {len(items)} items...")
    found_any = False

    for item in items:
        date_str = (
            item.get("date") or item.get("startDate") or item.get("localDate") or
            item.get("day") or item.get("startTime", "")
        )
        if not date_str:
            continue

        if not _is_target_date(str(date_str)):
            continue

        found_any = True
        sold_out    = item.get("soldOut", False) or item.get("isSoldOut", False)
        available   = item.get("available", True)
        unavailable = item.get("unavailable", False)
        closed      = item.get("closed", False)
        status      = item.get("status", "")

        print(f"  Aug 18 found: soldOut={sold_out}, available={available}, status={status}")

        if sold_out or unavailable or closed or status in ("SOLD_OUT", "UNAVAILABLE", "CLOSED"):
            return False
        if not available:
            return False
        return True

    if not found_any:
        print(f"  Aug 18 not in response (only {len(items)} items returned)")
        return None

    return None

# ── Playwright fallback ───────────────────────────────────────────────────────

def check_via_playwright(tour: dict) -> bool | None:
    """
    Opens the tour page with Playwright, intercepts ALL bokun.io responses,
    and checks availability for August 18.
    Returns True/False/None.
    """
    url  = tour["url"]
    name = tour["name"]

    bokun_responses: list[dict] = []
    discovered_channel = [None]
    discovered_product = [None]

    def on_response(response):
        try:
            resp_url = response.url
            if "bokun.io" not in resp_url:
                return
            # Log all bokun URLs for debugging
            print(f"  [bokun] {response.status} {resp_url[:100]}")

            # Try to extract channel/product from widget URLs like:
            # /online-sales/{channel}/experience/{product}/...
            if "/online-sales/" in resp_url and "/experience/" in resp_url:
                parts = resp_url.split("/")
                try:
                    idx = parts.index("online-sales")
                    discovered_channel[0] = parts[idx + 1]
                    pidx = parts.index("experience", idx)
                    discovered_product[0] = parts[pidx + 1].split("?")[0]
                except (ValueError, IndexError):
                    pass

            if response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    body = response.json()
                    bokun_responses.append({"url": resp_url, "body": body})
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent=UA,
            locale="en-GB",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.on("response", on_response)

        # Only block images/fonts, NOT JS or XHR
        page.route(
            "**/*.{png,jpg,jpeg,gif,ico,woff,woff2,ttf,eot}",
            lambda r: r.abort()
        )

        print(f"  Loading {url} ...")
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"  networkidle timeout (continuing): {e}")

        # Extra wait for lazy-loaded widgets
        time.sleep(random.uniform(8, 12))

        # Scroll down to trigger lazy loading of booking widget
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(3)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(3)

        # Log all frames for debug
        for frame in page.frames:
            if frame.url and frame.url != "about:blank":
                print(f"  [frame] {frame.url[:100]}")

        browser.close()

    # If we discovered channel/product via Playwright, try direct API
    ch = discovered_channel[0]
    pr = discovered_product[0]
    if ch and pr and (ch != tour.get("bokun_channel") or pr != tour.get("bokun_product")):
        print(f"  Discovered Bokun channel={ch} product={pr} — trying API...")
        result = check_via_bokun_api(ch, pr, name)
        if result is not None:
            return result

    # Parse intercepted responses
    if not bokun_responses:
        print(f"  No Bokun JSON responses captured.")
        return None

    print(f"  Captured {len(bokun_responses)} Bokun response(s).")
    for r in bokun_responses:
        result = _parse_bokun_response_data(r["body"], name)
        if result is not None:
            return result

    return None


def _is_target_date(date_str: str) -> bool:
    s = date_str
    return (
        s.startswith("2026-08-18") or
        s.startswith("18.08.2026") or
        s.startswith("08/18/2026")
    )

# ── Main availability check ───────────────────────────────────────────────────

def check_tour_availability(tour: dict) -> bool:
    """
    Try direct Bokun API first (fast), then Playwright fallback (slow).
    Returns True if August 18 is available, False otherwise.
    """
    name = tour["name"]

    # 1. Direct API (if we know the channel+product)
    if tour.get("bokun_channel") and tour.get("bokun_product"):
        result = check_via_bokun_api(tour["bokun_channel"], tour["bokun_product"], name)
        if result is not None:
            return result
        print("  Direct API inconclusive — falling back to Playwright.")

    # 2. Playwright
    result = check_via_playwright(tour)
    if result is not None:
        return result

    # 3. If still inconclusive, assume not yet available (safe default)
    print(f"  Could not determine availability for {name} — assuming not available.")
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
                time.sleep(random.uniform(2, 4))
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
