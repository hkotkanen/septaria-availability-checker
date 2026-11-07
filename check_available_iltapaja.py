# API returns stuff like this:
#{'2026-11-09': [],
# '2026-11-10': [{'object': 'class',
#   'id': 1951,
#   'name': 'Tiistain iltapaja',
#   'date': '2026-11-10',
#   'from': '16:30',
#   'to': '00:00',
#   'category_id': 2,
#   'description': {'object': 'description',
#    'text': '1.10.26 alkaen\nIltapaja on itsenäistä työskentelyä avoimessa pajassa klo 16.00-00.00. Tiistain iltapajassa pitää olla viimeistään 19.30. Henkilökunta ei ole paikalla klo 20.00 jälkeen. Paja pitää olla siivottu ja kunnossa seuraavana aamuna. Jos jäät työskentelmään klo 20.00 jälkeen, tilan vuokrauksesta tehdään vuokrasopimus.\nHuom! Mikäli tulet ensimmäistä kertaa pajalle työskentelemään, niin varaathan ajan ensin päiväpajalle.'},
#   'status': 'full',
#   'color': '3fbede',
#   'places_left': 0}],
# '2026-11-11': ...

from __future__ import annotations

import datetime
import fcntl
import httpx
import json
import logging
import os
import sys
from pathlib import Path

URL = "https://www.varaaheti.fi/septariapaja/fi/api/public/locations/kerasil_oy/views/classes/classes/available?date={}&date_range=week&search_next_date=true"
NTFY_TOPIC = "septaria-iltapaja-availabilities"
BOOKING_URL = "https://www.varaaheti.fi/septariapaja/fi/kerasil_oy/classes"

HERE = Path(__file__).parent
STATE_FILE = HERE / "state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def this_monday() -> datetime.date:
    """Returns datetime.date of prev monday (unless it's saturday or sunday, then returns next), needed by varaaheti API"""
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday())
    if today.weekday() >= 5:  # start considering next week on saturday
        monday += datetime.timedelta(weeks=1)
    return monday

def act(message: str, click: str = BOOKING_URL) -> None:
    """Act on a found availability"""
    log.info(f"Sending notification {message} to ntfy.sh")
    httpx.post(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        content=message.encode(encoding='utf-8'),
        headers={
            "Click": click,
            "Title": "Iltapaja available",
            "Priority": "high",
        },
    )

def save_state(state: dict[str, list[int]]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, STATE_FILE)

def load_state() -> dict[str, list[int]]:
    """
    State is json like where the list has class ids
    {"available": [
        1910,
        1911,
        ]
    }
    """
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"available": []}

def fetch_availability(week: str | None = None) -> dict:
    """Fetch availability data for a given week (current if left empty)"""
    week_to_fetch = week or this_monday()
    return httpx.get(URL.format(week_to_fetch)).json()
    
def fetch_availabilities() -> tuple[dict[int, dict], bool]:
    """Fetch availabilies for current and next two weeks"""
    found: dict[int, dict] = {}
    complete = True

    for n in range(3):
        week = (this_monday() + datetime.timedelta(weeks=n)).isoformat()
        log.info(f"Fetching classes for week {week}")
        try:
            days = fetch_availability(week)["data"]
        except Exception as e:
            log.error(f"failed to fetch week {week}: {e}")
            complete = False
            continue
        for classes in days.values():
            for c in classes:
                if c["status"] != "full":
                    found[c["id"]] = c
    return found, complete

def main() -> int:
     # Prevent overlapping runs if one hangs past the cron interval.
    lock = open(HERE / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.warning("previous run still going, skipping")
        return 0

    old = set(load_state()["available"])
    log.info(f"Old state availabilities: {old}")

    found, complete = fetch_availabilities()
    log.info(f"Found availabilities: {found}")

    for classid in found.keys() - old:
        match = found[classid]
        act(f"{match['name']} on {match['date']} with {match['places_left']} places left", click=match.get("url", BOOKING_URL))

    # On a partial fetch, keep old entries so we don't re-notify next run.
    save_state({"available": sorted(set(found) | (set() if complete else old))})
    return 0

if __name__=="__main__":
    sys.exit(main())