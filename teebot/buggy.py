"""Buggy bookings at TNGF: see what is free, book one, watch for a cancellation.

Usage:
    .venv/bin/python -m teebot.buggy show --date 20-11-2026
    .venv/bin/python -m teebot.buggy mine
    .venv/bin/python -m teebot.buggy book --date friday --session Afternoon            # rehearsal
    .venv/bin/python -m teebot.buggy book --date friday --session Afternoon --live --wait
    .venv/bin/python -m teebot.buggy watch --date 20-11-2026 --session Afternoon --live
    .venv/bin/python -m teebot.buggy cancel --date 27-11-2026 --session Afternoon --live

`book`, `watch` and `cancel` change nothing unless --live is given.
`--date friday` means the coming Friday (1 to 7 days from today), which is the
Friday whose booking window opens today when today is a Friday.
"""
import argparse
import fcntl
import json
import os
import random
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from .client import BOOKING, DEBUG_DIR, TIMEOUT, TngfClient, TngfError
from .group import ENV_FILE, booking_run_active
from .list_slots import IST

BUGGY_API = urljoin(BOOKING, "api/buggy/booking/")
COURSE_URL = "https://63v082fjye.execute-api.ap-south-1.amazonaws.com/production/api/course/1/"
# In the home folder so the project copy and the scheduled copy (see buggy-schedule.sh) share it.
LOCK_FILE = Path.home() / ".tngf-teebot.lock"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

LOGIN_LEAD = 75        # seconds before the window opens to log in
EARLY = 0.4            # seconds before the opening time to send the first request
OPEN_RETRY_FOR = 25    # seconds to keep asking after the opening time
OPEN_RETRY_GAP = 0.7

# Watcher pacing: a little over an hour between looks, never the same gap twice.
WATCH_GAP_MINUTES = (55, 85)
QUIET_FROM = (22, 0)   # no looks from 10:00 PM ...
QUIET_TO = (3, 30)     # ... to 3:30 AM


def log(line: str) -> None:
    print(f"[{datetime.now(IST):%a %d %b %H:%M:%S}] {line}", flush=True)


# ---- pure helpers (covered by tests/test_buggy.py) -----------------------

def resolve_date(text: str, today) -> str:
    """DD-MM-YYYY as given, or a weekday name for the coming one (1 to 7 days ahead)."""
    name = text.strip().lower()
    if name in WEEKDAYS:
        ahead = (WEEKDAYS.index(name) - today.weekday()) % 7 or 7
        return (today + timedelta(days=ahead)).strftime("%d-%m-%Y")
    datetime.strptime(text, "%d-%m-%Y")
    return text


def at_time(date_ddmmyyyy: str, label: str) -> datetime:
    return datetime.strptime(f"{date_ddmmyyyy} {label}", "%d-%m-%Y %I:%M %p").replace(tzinfo=IST)


def opens_at(conf: dict, date_ddmmyyyy: str) -> datetime:
    """The site opens a day's buggies `booking_opening_day` days away, at the opening time."""
    moment = at_time(date_ddmmyyyy, conf.get("booking_opening_time") or "06:00 AM")
    return moment + timedelta(days=int(conf.get("booking_opening_day") or 0))


def cutoff_at(conf: dict, date_ddmmyyyy: str) -> Optional[datetime]:
    label = conf.get("booking_cutoff_time")
    return at_time(date_ddmmyyyy, label) if label else None


def session_state(conf: dict, bookings: list, my_id) -> dict:
    """Counts for one session. A cancelled booking frees its buggy; anything else holds one."""
    rows = [b for b in bookings if b.get("session_name") == conf.get("session_name")]
    held = [b for b in rows if b.get("status") != "Cancelled"]
    mine = [b for b in held if b.get("status") == "Booked" and str(b.get("booked_by_id")) == str(my_id)]
    return {
        "total": int(conf.get("buggies") or 0),
        "taken": len(held),
        "free": max(int(conf.get("buggies") or 0) - len(held), 0),
        "mine": mine,
    }


def in_quiet_hours(moment: datetime) -> bool:
    now = (moment.hour, moment.minute)
    return now >= QUIET_FROM or now < QUIET_TO


def next_look(now: datetime, rng=random) -> datetime:
    """When the watcher looks again: 55 to 85 minutes on, pushed past the quiet hours."""
    moment = now + timedelta(minutes=rng.uniform(*WATCH_GAP_MINUTES))
    if in_quiet_hours(moment):
        wake = moment.replace(hour=QUIET_TO[0], minute=QUIET_TO[1], second=0, microsecond=0)
        if (moment.hour, moment.minute) >= QUIET_FROM:
            wake += timedelta(days=1)
        moment = wake + timedelta(minutes=rng.uniform(0, 12))
    return moment


# ---- site calls ----------------------------------------------------------

@contextmanager
def site_session():
    """One logged-in session at a time: the site ends an older session on a new login."""
    DEBUG_DIR.mkdir(exist_ok=True)
    if booking_run_active():
        raise TngfError("A tee-time booking run is going; logging in now would end its session.")
    load_dotenv(ENV_FILE)
    member_id = os.environ.get("TNGF_MEMBER_ID", "").strip()
    password = os.environ.get("TNGF_PASSWORD", "")
    if not member_id or not password:
        raise TngfError("Fill in TNGF_MEMBER_ID and TNGF_PASSWORD in .env first.")
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)   # waits for another buggy command to finish
        client = TngfClient()
        try:
            client.login(member_id, password)
            yield client
        finally:
            client.logout()


def load_day(client: TngfClient, date_ddmmyyyy: str) -> tuple:
    """The buggy page for a date: (session settings, every booking row for the day)."""
    resp = client.http.get(urljoin(BOOKING, "buggybooking"), params={"date": date_ddmmyyyy}, timeout=TIMEOUT)
    resp.raise_for_status()
    tag = BeautifulSoup(resp.text, "html.parser").find("script", id="__NEXT_DATA__")
    if tag is None or not tag.string:
        raise TngfError("Buggy page had no data block.")
    props = json.loads(tag.string).get("props", {}).get("pageProps", {})
    conf, booked = props.get("confResponse") or {}, props.get("bookingResponse") or {}
    for part in (conf, booked):
        if part.get("error_msg") or part.get("unauthorized"):
            raise TngfError(f"Buggy page refused: {part.get('error_msg') or 'unauthorized'}")
    if conf.get("data") is None:
        raise TngfError("No buggy data; the session is no longer valid (logged in somewhere else?)")
    return conf["data"], booked.get("data") or []


def find_conf(confs: list, session: str) -> dict:
    for conf in confs:
        if str(conf.get("session_name", "")).lower() == session.lower():
            return conf
    names = ", ".join(str(c.get("session_name")) for c in confs) or "none"
    raise TngfError(f"No buggy session called {session!r} on that day (sessions: {names}).")


def refusal(resp: requests.Response) -> Optional[str]:
    """None if the site accepted the request, else its message."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.ok and isinstance(body, dict) and body.get("error") is None:
        return None
    if isinstance(body, dict) and (body.get("message") or body.get("error")):
        return f"{body.get('message') or body.get('error')} (HTTP {resp.status_code})"
    return f"HTTP {resp.status_code}"


def take_buggy(client: TngfClient, conf: dict, date_ddmmyyyy: str) -> requests.Response:
    """Step 1 of the site's form: claim a buggy for the session."""
    return client.http.post(
        BUGGY_API, params={"nomail": "yes"},
        json={"date": date_ddmmyyyy, "configuration": conf["url"], "course": COURSE_URL,
              "status": "Booked", "booked_by_id": client.user.get("id")},
        headers=client.api_headers(), timeout=TIMEOUT,
    )


def patch_booking(client: TngfClient, url: str, body: dict, mail: bool = True) -> requests.Response:
    booking_id = url.rstrip("/").split("/")[-1]
    return client.http.patch(
        urljoin(BUGGY_API, f"{booking_id}/"), params=None if mail else {"nomail": "yes"},
        json=body, headers=client.api_headers(), timeout=TIMEOUT,
    )


def complete_booking(client: TngfClient, conf: dict, date_ddmmyyyy: str, hole: int) -> Optional[str]:
    """Both steps of the site's form. Returns None when booked, else why not."""
    resp = take_buggy(client, conf, date_ddmmyyyy)
    problem = refusal(resp)
    if problem:
        return problem
    url = resp.json().get("url")
    if not url:
        return "the site accepted the request but returned no booking"
    # Step 2: the starting hole. Without it the site's own form cancels the claim.
    resp = patch_booking(client, url, {"hole_no": hole, "booked_by_id": client.user.get("id")})
    problem = refusal(resp)
    if problem:
        undo = patch_booking(client, url, {"status": "Cancelled"}, mail=False)
        return f"hole {hole} was refused: {problem}; claim released (HTTP {undo.status_code})"
    return None


def my_booking(client: TngfClient, conf: dict, date_ddmmyyyy: str) -> Optional[dict]:
    _, bookings = load_day(client, date_ddmmyyyy)
    mine = session_state(conf, bookings, client.user.get("id"))["mine"]
    return mine[0] if mine else None


def describe_day(date_ddmmyyyy: str, confs: list, bookings: list, my_id, now: datetime) -> None:
    day = datetime.strptime(date_ddmmyyyy, "%d-%m-%Y")
    print(f"\nBuggies for {day:%a %d %b %Y}:")
    for conf in sorted(confs, key=lambda c: c.get("session", 0)):
        state = session_state(conf, bookings, my_id)
        opens, cutoff = opens_at(conf, date_ddmmyyyy), cutoff_at(conf, date_ddmmyyyy)
        if now < opens:
            window = f"opens {opens:%a %d %b %I:%M %p}"
        elif cutoff and now > cutoff:
            window = f"closed since {cutoff:%I:%M %p}"
        else:
            window = "open" + (f" until {cutoff:%a %I:%M %p}" if cutoff else "")
        print(f"  {conf.get('session_name')}: {state['free']} free of {state['total']} ({window})")
        for b in state["mine"]:
            print(f"    yours: hole {b.get('hole_no') or '?'}, booking {b['url'].rstrip('/').split('/')[-1]}")


# ---- commands ------------------------------------------------------------

def cmd_show(args) -> int:
    date = resolve_date(args.date, datetime.now(IST).date())
    with site_session() as client:
        confs, bookings = load_day(client, date)
        describe_day(date, confs, bookings, client.user.get("id"), datetime.now(IST))
    return 0


def cmd_mine(args) -> int:
    with site_session() as client:
        resp = client.http.get(
            BUGGY_API,
            params={"status": "Booked", "date__gte": datetime.now(IST).strftime("%d-%m-%Y"),
                    "booked_by__id": client.user.get("id")},
            headers=client.api_headers(), timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise TngfError(f"Buggy booking list failed: HTTP {resp.status_code}")
        data = resp.json()
    rows = data.get("results", []) if isinstance(data, dict) else data
    rows.sort(key=lambda b: datetime.strptime(b["date"], "%d-%m-%Y"))
    print(f"\nUpcoming buggy bookings: {len(rows)}")
    for b in rows:
        day = datetime.strptime(b["date"], "%d-%m-%Y")
        print(f"  {day:%a %d %b %Y}, {b.get('session_name')}, hole {b.get('hole_no') or '?'}")
    return 0


def cmd_book(args) -> int:
    date = resolve_date(args.date, datetime.now(IST).date())
    with site_session() as client:
        confs, bookings = load_day(client, date)
        conf = find_conf(confs, args.session)
        state = session_state(conf, bookings, client.user.get("id"))
        opens, cutoff = opens_at(conf, date), cutoff_at(conf, date)
        now = datetime.now(IST)
        day = datetime.strptime(date, "%d-%m-%Y")
        log(f"Buggy for {day:%a %d %b %Y}, {conf['session_name']}, hole {args.hole}: "
            f"{state['free']} free of {state['total']}; window opens {opens:%a %d %b %I:%M %p}.")
        if state["mine"]:
            log("ALREADY BOOKED: you have a buggy for this session. Nothing to do.")
            return 0
        if cutoff and now > cutoff:
            log(f"CLOSED: booking for this session ended at {cutoff:%I:%M %p}.")
            return 1
        wait = (opens - now).total_seconds()
        if not args.live:
            log("REHEARSAL: nothing booked. " + (
                f"The window opens in {wait / 3600:.1f} hours." if wait > 0
                else "A live run would book now." if state["free"] else "No buggy is free right now."))
            return 0
        if wait > 0 and not args.wait:
            log("NOT OPEN YET: add --wait to wait for the window.")
            return 1
        if wait > LOGIN_LEAD + 30:
            # Too early to hold a session: come back just before the window.
            client.logout()
            log(f"Waiting until {opens:%I:%M %p}.")
            while (opens - datetime.now(IST)).total_seconds() > LOGIN_LEAD:
                time.sleep(min(30, max((opens - datetime.now(IST)).total_seconds() - LOGIN_LEAD, 1)))
            load_dotenv(ENV_FILE)
            client.login(os.environ["TNGF_MEMBER_ID"].strip(), os.environ["TNGF_PASSWORD"])
        elif wait <= 0 and not state["free"]:
            log("FULL: no buggy is free. Use the watch command to catch a cancellation.")
            return 1
        while (opens - datetime.now(IST)).total_seconds() > EARLY:
            time.sleep(min(0.2, max((opens - datetime.now(IST)).total_seconds() - EARLY, 0.01)))

        deadline = max(opens.timestamp(), time.time()) + (OPEN_RETRY_FOR if wait > 0 else 0)
        last = None
        while True:
            try:
                problem = complete_booking(client, conf, date, args.hole)
            except requests.RequestException as err:
                problem = f"no answer ({type(err).__name__})"
                if my_booking(client, conf, date):
                    problem = None
            if problem is None:
                break
            if problem != last:
                log(f"Refused: {problem}")
                last = problem
            if time.time() >= deadline:
                log("NOT BOOKED.")
                return 1
            time.sleep(OPEN_RETRY_GAP)
        booked = my_booking(client, conf, date)
        if not booked:
            log("UNCONFIRMED: the site accepted the booking but it is not in the list. Check the site.")
            return 1
        log(f"BOOKED: buggy for {day:%a %d %b %Y}, {conf['session_name']}, hole {booked.get('hole_no')}.")
    return 0


def cmd_watch(args) -> int:
    date = resolve_date(args.date, datetime.now(IST).date())
    day = datetime.strptime(date, "%d-%m-%Y")
    log(f"Watching for a free buggy: {day:%a %d %b %Y}, {args.session}, hole {args.hole}"
        + ("" if args.live else " (rehearsal: will report, not book)") + ".")
    cutoff = None
    while True:
        now = datetime.now(IST)
        if cutoff and now > cutoff:
            log(f"STOPPED: booking for this session closed at {cutoff:%I:%M %p}. No buggy came free.")
            return 1
        if now.date() > day.date():
            log("STOPPED: the day has passed.")
            return 1
        if in_quiet_hours(now):
            log("Quiet hours: not looking.")
        else:
            try:
                with site_session() as client:
                    confs, bookings = load_day(client, date)
                    conf = find_conf(confs, args.session)
                    cutoff = cutoff_at(conf, date)
                    state = session_state(conf, bookings, client.user.get("id"))
                    if state["mine"]:
                        log("ALREADY BOOKED: you have a buggy for this session. Watcher done.")
                        return 0
                    if datetime.now(IST) < opens_at(conf, date):
                        log("Window not open yet.")
                    elif not state["free"]:
                        log(f"None free ({state['taken']} of {state['total']} taken).")
                    elif not args.live:
                        log(f"FOUND: {state['free']} free. Rehearsal, so not booking.")
                        return 0
                    else:
                        log(f"{state['free']} free: booking.")
                        problem = complete_booking(client, conf, date, args.hole)
                        if problem is None and my_booking(client, conf, date):
                            log(f"BOOKED: buggy for {day:%a %d %b %Y}, {conf['session_name']}, hole {args.hole}.")
                            return 0
                        log(f"Not booked: {problem or 'accepted, but not in the list; check the site'}")
            except (TngfError, requests.RequestException) as err:
                log(f"Look skipped: {err}")
        wake = next_look(datetime.now(IST))
        if cutoff and wake > cutoff:
            # One last look a few minutes before the session closes.
            last = cutoff - timedelta(minutes=random.uniform(4, 9))
            wake = last if last > datetime.now(IST) else cutoff + timedelta(seconds=30)
        log(f"Next look at {wake:%a %I:%M %p}.")
        while datetime.now(IST) < wake:
            time.sleep(min(60, max((wake - datetime.now(IST)).total_seconds(), 1)))


def cmd_cancel(args) -> int:
    date = resolve_date(args.date, datetime.now(IST).date())
    day = datetime.strptime(date, "%d-%m-%Y")
    with site_session() as client:
        confs, _ = load_day(client, date)
        conf = find_conf(confs, args.session)
        booked = my_booking(client, conf, date)
        if not booked:
            log(f"NOTHING TO CANCEL: no buggy of yours on {day:%a %d %b %Y}, {conf['session_name']}.")
            return 1
        if not args.live:
            log(f"REHEARSAL: would cancel your buggy on {day:%a %d %b %Y}, {conf['session_name']}.")
            return 0
        problem = refusal(patch_booking(client, booked["url"], {"status": "Cancelled"}))
        if problem or my_booking(client, conf, date):
            log(f"NOT CANCELLED: {problem or 'the booking is still in the list'}")
            return 1
        log(f"CANCELLED: buggy on {day:%a %d %b %Y}, {conf['session_name']}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Buggy bookings at TNGF.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, live=True):
        p.add_argument("--date", required=True, help="DD-MM-YYYY, or a weekday name for the coming one")
        p.add_argument("--session", default="Afternoon", help="Morning or Afternoon (default Afternoon)")
        if live:
            p.add_argument("--live", action="store_true", help="really do it (default: rehearse)")

    show = sub.add_parser("show", help="free buggies for a date")
    show.add_argument("--date", required=True, help="DD-MM-YYYY, or a weekday name for the coming one")
    show.set_defaults(run=cmd_show)
    sub.add_parser("mine", help="your upcoming buggy bookings").set_defaults(run=cmd_mine)
    book = sub.add_parser("book", help="book one buggy")
    common(book)
    book.add_argument("--hole", type=int, choices=(1, 10), default=1, help="starting hole (default 1)")
    book.add_argument("--wait", action="store_true", help="if the window is not open yet, wait for it")
    book.set_defaults(run=cmd_book)
    watch = sub.add_parser("watch", help="look about hourly and book the first free buggy")
    common(watch)
    watch.add_argument("--hole", type=int, choices=(1, 10), default=1, help="starting hole (default 1)")
    watch.set_defaults(run=cmd_watch)
    cancel = sub.add_parser("cancel", help="cancel your buggy for a date and session")
    common(cancel)
    cancel.set_defaults(run=cmd_cancel)

    args = parser.parse_args()
    try:
        return args.run(args)
    except ValueError:
        print(f"Date must be DD-MM-YYYY or a weekday name, got {args.date!r}.", file=sys.stderr)
        return 2
    except (TngfError, requests.RequestException) as err:
        log(f"Stopped: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
