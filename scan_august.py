import sys, uuid, datetime
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

LIVA_TOURS = [
    ("Zodiac Boat Tour (icelagoon.is)", "fun-adventure"),
    ("Amphibian Tour (icelagoon.is)", "adventure-tour"),
]
BOKUN_TOUR = ("Adventure Tour (icelagoon.com)", "50c3e856-1b67-4450-8dbb-aa6d293564f2", "14059", 9624)

print("=" * 70)
print("SCAN AGOSTO 2026 — tutti i giorni, tutti i tour")
print("=" * 70)

for name, tag in LIVA_TOURS:
    print(f"\n[{name}] — LIVA")
    url = (f"https://explore.liva.is/jokulsarlon/api/events/calendar-availability"
           f"?experienceTag={tag}&year=2026&month=8")
    try:
        resp = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        print(f"  {len(data)} giorni ricevuti dall'API.")
        any_available = False
        for day in sorted(data, key=lambda d: d.get("startTime", "")):
            start = day.get("startTime", "")
            if not start.startswith("2026-08"):
                continue
            available = day.get("available", False)
            slots = day.get("availableSlots", 0)
            date_str = start[:10]
            if available and slots > 0:
                any_available = True
                print(f"  {date_str}: available={available}  slots={slots}  <-- DISPONIBILE")
            else:
                print(f"  {date_str}: available={available}  slots={slots}")
        if not any_available:
            print("  >>> Nessun giorno di agosto disponibile.")
    except Exception as e:
        print(f"  ERRORE: {e}", file=sys.stderr)

name, seller, product, cat_id = BOKUN_TOUR
print(f"\n[{name}] — Bokun")
session = str(uuid.uuid4())
url = f"https://widgets.bokun.io/widgets/{seller}/activity/{product}/2026/8"
headers = {
    "User-Agent": UA,
    "x-bokun-source": "WIDGET",
    "x-bokun-currency": "ISK",
    "x-bokun-language": "en_GB",
    "x-bokun-session": session,
    "Content-Type": "application/json",
}
params = {"currency": "ISK", "sessionId": session, "lang": "en_GB"}
body = {"pricingCategories": [{"id": cat_id, "quantity": 2}], "giftCard": False}
try:
    resp = requests.post(url, headers=headers, params=params, json=body, timeout=30)
    print(f"  HTTP {resp.status_code}")
    resp.raise_for_status()
    calendar = resp.json().get("calendar", {})
    weeks = calendar.get("weeks", [])
    any_available = False
    rows = []
    for week in weeks:
        for day in week.get("days", []):
            date_ms = day.get("dateObj", 0)
            if not date_ms:
                continue
            day_date = datetime.datetime.utcfromtimestamp(date_ms / 1000).date()
            if day_date.month != 8 or day_date.year != 2026:
                continue
            status = day.get("activityAvailabilityStatus", "")
            best_count = 0
            for slot in day.get("availabilities", []):
                count = slot.get("availabilityCount", 0)
                slot_status = slot.get("activityAvailability", {}).get("availabilityStatus", "")
                if slot_status == "AVAILABLE":
                    best_count = max(best_count, count)
            rows.append((day_date, status, best_count))
    for day_date, status, best_count in sorted(rows):
        if status == "AVAILABLE" and best_count >= 2:
            any_available = True
            print(f"  {day_date}: status={status}  max_slot_count={best_count}  <-- DISPONIBILE")
        else:
            print(f"  {day_date}: status={status}  max_slot_count={best_count}")
    if not any_available:
        print("  >>> Nessun giorno di agosto disponibile (>=2 posti).")
except Exception as e:
    print(f"  ERRORE: {e}", file=sys.stderr)

print("\n" + "=" * 70)
print("SCAN COMPLETATO")
print("=" * 70)
