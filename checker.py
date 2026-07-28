"""
Jökulsárlón Boat Tours — availability checker for August 18.

Monitors three tour pages and alerts via WhatsApp (Twilio) as soon as
August 18 opens up.

Booking systems discovered:
  - icelagoon.is  → LIVA (liva.is / explore.liva.is)
  - icelagoon.com → Bokun (widgets.bokun.io)

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
TARGET_DAY_STR    = "2026-08-18"

# Discovered from live page inspection:
# icelagoon.is — LIVA booking system
LIVA_ZODIAC_ID    = "32e3d754-039c-49eb-9768-db02f5264ab3"
LIVA_AMPHIBIAN_ID = "86bff13c-560a-4e30-a493-d93393059f55"

# icelagoon.com — Bokun booking system (channel UUID from live page)
BOKUN_CHANNEL_UUID = "50c3e856-1b67-4450-8dbb-aa6d293564f2"
BOKUN_PRODUCT_ID   = "14059"

TOURS = [
    {
        "name":    "Zodiac Boat Tour (icelagoon.is)",
        "url":     "https://icelagoon.is/tours/zodiac-boat-tour/",
        "system":  "liva",
        "liva_id": LIVA_ZODIAC_ID,
    },
    {
        "name":    "Amphibian Tour (icelagoon.is)",
        "url":     "https://icelagoon.is/tours/amphibian-tours/",
        "system":  "liva",
        "liva_id": LIVA_AMPHIBIAN_ID,
    },
    {
        "name":    "Adventure Tour (icelagoon.com)",
        "url":     "https://www.icelagoon.com/adventure-tour/",
        "system":  "bokun",
        "bokun_channel": BOKUN_CHANNEL_UUID,
        "bokun_product": BOKUN_PRODUCT_ID,
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
    return {"notified": [], "last_check": None, "errors": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

# ── LIVA API check (icelagoon.is) ─────────────────────────────────────────────

LIVA_API_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Origin": "https://explore.liva.is",
    "Referer": "https://explore.liva.is/",
}

def check_liva_api(liva_id: str, tour_name: str) -> bool | None:
    """
    Try LIVA API endpoints directly to check August 18 availability.
    Returns True/False/None (None = inconclusive, fall back to Playwright).
    """
    endpoints = [
        f"https://explore.liva.is/api/v1/products/{liva_id}/availability?year=2026&month=8",
        f"https://explore.liva.is/api/v1/products/{liva_id}/slots?date=2026-08-18",
        f"https://api.liva.is/v1/products/{liva_id}/availability?startDate=2026-08-01&endDate=2026-08-31",
        f"https://widgets.liva.is/jokulsarlon/api/product/{liva_id}/availability?month=2026-08",
    ]

    for endpoint in endpoints:
        try:
            print(f"  Trying LIVA API: {endpoint}")
            resp = requests.get(endpoint, headers=LIVA_API_HEADERS, timeout=15)
            print(f"  → HTTP {resp.status_code}")
            if resp.status_code not in (200, 201):
                continue
            data = resp.json()
            print(f"  → Got data: {str(data)[:200]}")
            result = _parse_availability_data(data, tour_name)
            if result is not None:
                return result
        except Exception as e:
            print(f"  LIVA API error: {e}")

    return None


# ── Playwright check (intercepts all booking system calls) ────────────────────

def check_via_playwright(tour: dict) -> bool | None:
    """
    Load the tour page with Playwright, intercept all booking system API calls
    (both LIVA and Bokun), and check August 18 availability.
    """
    url  = tour["url"]
    name = tour["name"]

    captured: list[dict] = []

    def on_response(response):
        try:
            resp_url = response.url
            is_booking = (
                "liva.is" in resp_url or
                "bokun.io" in resp_url
            )
            if not is_booking:
                return

            print(f"  [intercept] {response.status} {resp_url[:120]}")

            if response.status != 200:
                return
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return

            body = response.json()
            captured.append({"url": resp_url, "body": body})
        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="en-GB",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.on("response", on_response)

        # Block only images/fonts — keep all JS and XHR
        page.route(
            "**/*.{png,jpg,jpeg,gif,ico,woff,woff2,ttf,eot}",
            lambda r: r.abort()
        )

        print(f"  Loading {url} ...")
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"  networkidle timeout (continuing): {e}")

        # Scroll to trigger lazy-loaded booking widget
        time.sleep(3)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
        time.sleep(4)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(6)

        browser.close()

    if not captured:
        print(f"  No booking API JSON captured for {name}.")
        return None

    print(f"  Captured {len(captured)} booking response(s).")
    for r in captured:
        result = _parse_availability_data(r["body"], name)
        if result is not None:
            return result

    return None


# ── Shared availability parser ────────────────────────────────────────────────

def _parse_availability_data(data, tour_name: str) -> bool | None:
    """
    Parse any booking system availability response.
    Handles lists, dicts with 'days'/'items'/'dates'/'slots' keys, etc.
    Returns True/False/None.
    """
    # Flatten to a list of items
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (
            data.get("days") or data.get("items") or data.get("dates") or
            data.get("slots") or data.get("availability") or
            data.get("availabilities") or data.get("results") or []
        )
        if not isinstance(items, list):
            items = []
    else:
        return None

    if not items:
        return None

    print(f"  Parsing {len(items)} items...")

    for item in items:
        if not isinstance(item, dict):
            continue

        # Find date field
        date_str = (
            item.get("date") or item.get("startDate") or item.get("localDate") or
            item.get("day") or item.get("start") or item.get("startTime", "")
        )
        if not date_str or not _is_target_date(str(date_str)):
            continue

        # Check availability fields
        sold_out    = item.get("soldOut", False) or item.get("isSoldOut", False)
        available   = item.get("available", True)
        unavailable = item.get("unavailable", False)
        closed      = item.get("closed", False)
        status      = str(item.get("status", "")).upper()

        print(f"  Aug 18 found: soldOut={sold_out}, available={available}, "
              f"unavailable={unavailable}, status={status}")

        if sold_out or unavailable or closed or status in ("SOLD_OUT", "UNAVAILABLE", "CLOSED", "FULL"):
            return False
        if not available:
            return False
        return True

    return None


def _is_target_date(s: str) -> bool:
    return (
        s.startswith("2026-08-18") or
        s.startswith("18.08.2026") or
        s.startswith("08/18/2026")
    )


# ── Main availability check per tour ─────────────────────────────────────────

def check_tour_availability(tour: dict) -> bool:
    """
    Try direct API first (fast), then Playwright (slow).
    Returns True if August 18 is available.
    """
    name = tour["name"]

    # 1. Direct API
    if tour["system"] == "liva":
        result = check_liva_api(tour["liva_id"], name)
    else:
        result = _check_bokun_api_direct(
            tour["bokun_channel"], tour["bokun_product"], name
        )

    if result is not None:
        return result

    print("  Direct API inconclusive — trying Playwright...")

    # 2. Playwright fallback
    result = check_via_playwright(tour)
    if result is not None:
        return result

    print(f"  Still inconclusive — assuming not available yet.")
    return False


def _check_bokun_api_direct(channel: str, product: str, name: str) -> bool | None:
    """Try Bokun availability API directly."""
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Origin": "https://widgets.bokun.io",
        "Referer": f"https://widgets.bokun.io/online-sales/{channel}/experience-calendar/{product}",
    }
    endpoints = [
        f"https://widgets.bokun.io/online-sales/{channel}/experience/{product}/availability?month=2026-08",
        f"https://widgets.bokun.io/online-sales/{channel}/experience/{product}/calendar?year=2026&month=8",
    ]
    for ep in endpoints:
        try:
            print(f"  Trying Bokun API: {ep}")
            r = requests.get(ep, headers=headers, timeout=15)
            print(f"  → HTTP {r.status_code}, content-type: {r.headers.get('content-type','')[:40]}")
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                result = _parse_availability_data(r.json(), name)
                if result is not None:
                    return result
        except Exception as e:
            print(f"  Bokun API error: {e}")
    return None


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
                    print(f"  Error: {e}", file=sys.stderr)
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
