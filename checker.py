"""
Jökulsárlón Boat Tours — availability checker for August 18 (min 2 people).

Booking systems:
  icelagoon.is  → LIVA (explore.liva.is) — direct GET API
  icelagoon.com → Bokun (widgets.bokun.io) — direct POST API

No browser/Playwright needed — pure HTTP requests.

Required env vars:
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM / WA_TO
Optional:
  SIMULATE_AVAILABLE=1  → force "available" for testing
"""

import os, json, time, random, sys, uuid
from datetime import datetime, timezone, date

import requests

# ── Config ────────────────────────────────────────────────────────────────────

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN  = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM        = os.environ["TWILIO_FROM"]
WA_TO              = os.environ["WA_TO"]
SIMULATE           = os.environ.get("SIMULATE_AVAILABLE", "0") == "1"

TARGET_LABEL  = "18 agosto 2026"
TARGET_DATE   = date(2026, 8, 18)
MIN_PEOPLE    = 2   # notify only if at least this many seats are available

TOURS = [
    {
        "name":     "Zodiac Boat Tour (icelagoon.is)",
        "url":      "https://icelagoon.is/tours/zodiac-boat-tour/",
        "system":   "liva",
        "liva_tag": "fun-adventure",
    },
    {
        "name":     "Amphibian Tour (icelagoon.is)",
        "url":      "https://icelagoon.is/tours/amphibian-tours/",
        "system":   "liva",
        "liva_tag": "adventure-tour",
    },
    {
        "name":          "Adventure Tour (icelagoon.com)",
        "url":           "https://www.icelagoon.com/adventure-tour/",
        "system":        "bokun",
        "bokun_seller":  "50c3e856-1b67-4450-8dbb-aa6d293564f2",
        "bokun_product": "14059",
        "bokun_cat_id":  9624,   # pricing category ID for Adult
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

# ── LIVA check (icelagoon.is) ─────────────────────────────────────────────────

def check_liva(tour: dict) -> bool:
    """
    GET calendar-availability for August 2026 and check if Aug 18
    has available=True AND availableSlots >= MIN_PEOPLE.
    """
    tag = tour["liva_tag"]
    url = (
        f"https://explore.liva.is/jokulsarlon/api/events/calendar-availability"
        f"?experienceTag={tag}&year=2026&month=8"
    )
    print(f"  LIVA: {url}")
    resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    print(f"  Got {len(data)} days.")

    for day in data:
        if not day.get("startTime", "").startswith("2026-08-18"):
            continue
        available   = day.get("available", False)
        slots       = day.get("availableSlots", 0)
        print(f"  Aug 18: available={available}, slots={slots}")
        return bool(available) and slots >= MIN_PEOPLE

    print("  Aug 18 not found.")
    return False

# ── Bokun check (icelagoon.com) ───────────────────────────────────────────────

def check_bokun(tour: dict) -> bool:
    """
    POST to Bokun widget API for August 2026 availability.
    Checks if Aug 18 has activityAvailabilityStatus=AVAILABLE and at least
    one time slot with availabilityCount >= MIN_PEOPLE.
    """
    seller  = tour["bokun_seller"]
    product = tour["bokun_product"]
    cat_id  = tour["bokun_cat_id"]
    session = str(uuid.uuid4())

    url = f"https://widgets.bokun.io/widgets/{seller}/activity/{product}/2026/8"
    headers = {
        "User-Agent":        UA,
        "x-bokun-source":    "WIDGET",
        "x-bokun-currency":  "ISK",
        "x-bokun-language":  "en_GB",
        "x-bokun-session":   session,
        "Content-Type":      "application/json",
    }
    params = {"currency": "ISK", "sessionId": session, "lang": "en_GB"}
    body   = {"pricingCategories": [{"id": cat_id, "quantity": MIN_PEOPLE}], "giftCard": False}

    print(f"  Bokun POST: {url}")
    resp = requests.post(url, headers=headers, params=params, json=body, timeout=30)
    print(f"  → HTTP {resp.status_code}")
    resp.raise_for_status()

    calendar = resp.json().get("calendar", {})
    weeks = calendar.get("weeks", [])

    for week in weeks:
        for day in week.get("days", []):
            date_ms = day.get("dateObj", 0)
            day_date = datetime.utcfromtimestamp(date_ms / 1000).date()
            if day_date != TARGET_DATE:
                continue

            status = day.get("activityAvailabilityStatus", "")
            print(f"  Aug 18 day status: {status}")

            if status != "AVAILABLE":
                return False

            # Check individual time slots for MIN_PEOPLE seats
            for slot in day.get("availabilities", []):
                count = slot.get("availabilityCount", 0)
                slot_status = slot.get("activityAvailability", {}).get("availabilityStatus", "")
                start_time  = slot.get("activityAvailability", {}).get("startTime", "?")
                print(f"    Slot {start_time}: status={slot_status}, count={count}")
                if slot_status == "AVAILABLE" and count >= MIN_PEOPLE:
                    print(f"    → FOUND slot with {count} seats at {start_time}!")
                    return True

            print("  No slot with enough seats.")
            return False

    print("  Aug 18 not found in Bokun calendar.")
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

LOOP_INTERVAL_MIN = 6   # minutes between checks
LOOP_DURATION_MIN = 55  # total loop duration per job (stay under GitHub's 1h limit)

def run_checks(state: dict) -> dict:
    """Run one full check cycle across all tours. Returns updated state."""
    now = datetime.now(timezone.utc).isoformat()
    print(f"\n[{now}] Checking Jökulsárlón for {TARGET_LABEL} ({MIN_PEOPLE} people)...")

    notified: list[str] = state.get("notified", [])

    for tour in TOURS:
        name = tour["name"]
        if name in notified:
            print(f"  [SKIP] {name} — already notified.")
            continue

        print(f"\n  [CHECK] {name}")
        time.sleep(random.uniform(1, 2))

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
                f"JOKULSARLON — {TARGET_LABEL} DISPONIBILE per {MIN_PEOPLE} persone!\n"
                f"Tour: {name}\n"
                f"Prenota subito: {tour['url']}"
            )
            notified.append(name)
        else:
            print("  Not available.")

    state["notified"] = notified
    state["last_check"] = now

    errors = state.get("errors", 0)
    if errors > 0 and errors % 6 == 0:
        try:
            send_whatsapp(f"Jokulsarlon Pinger: {errors} errori consecutivi. Controlla GitHub.")
        except Exception:
            pass

    return state


def main():
    state = load_state()

    iterations = LOOP_DURATION_MIN // LOOP_INTERVAL_MIN  # e.g. 55 // 5 = 11

    for i in range(iterations):
        if i > 0:
            print(f"\nWaiting {LOOP_INTERVAL_MIN} min before next check...")
            time.sleep(LOOP_INTERVAL_MIN * 60)

        state = run_checks(state)
        save_state(state)

        # Stop early if all tours already notified
        if len(state.get("notified", [])) == len(TOURS):
            print("\nAll tours notified — stopping loop.")
            break

    print("\nJob complete.")


if __name__ == "__main__":
    main()
