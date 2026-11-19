"""Book a tee slot for your group.

Without --live this is a rehearsal: it logs in, works out which slots it would
try and in what order, and stops. With --live it holds and books one slot.

When the window opens, requests for the preferred slots overlap (see race.py),
so one slow reply cannot hold up the rest. At most one slot is booked; any
other slot it managed to hold is released straight away.

Usage:
    .venv/bin/python -m teebot.book --target "06:30 AM"                 # rehearsal, next Sunday
    .venv/bin/python -m teebot.book --target "06:30 AM" --live --wait   # wait for the window, then book
"""
import argparse
import os
import queue
import sys
import threading
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv

from .client import BOOKING, TIMEOUT, TngfClient, TngfError
from .list_slots import IST, next_sunday
from .race import ERROR, HELD, NOT_OPEN, OTHER, SESSION, TAKEN, Race, Reply

LOGIN_LEAD = 120      # log in this many seconds before the window opens
TRY_FOR = 30          # keep trying this many seconds after the window opens
WARM_LEAD = 10        # open the connections this many seconds before the window opens
MAX_INFLIGHT = 3      # requests in flight for one slot at the same time
HOLD_TIMEOUT = (5, 15)  # seconds to connect, seconds to wait for the reply


def parse_time(label: str) -> datetime:
    return datetime.strptime(label.strip().upper(), "%I:%M %p")


def minutes(label: str) -> int:
    t = parse_time(label)
    return t.hour * 60 + t.minute


def pick_candidates(day: dict, session: str, target: str, earliest: str, latest: str, holes: list,
                    include_taken: bool = False) -> list:
    """Free member slots in the window, closest to the target first."""
    free = {str(h): {e["slot"] for e in entries} for h, entries in (day.get("available_slots") or {}).items()}
    out = []
    for block in (day.get("templates") or {}).get("template_details") or []:
        if block["guest_only"] or block["session_name"].lower() != session.lower():
            continue
        if block["hole_no"] not in holes:
            continue
        for label in block["slots"]:
            if not include_taken and label not in free.get(str(block["hole_no"]), set()):
                continue
            if earliest and minutes(label) < minutes(earliest):
                continue
            if latest and minutes(label) > minutes(latest):
                continue
            out.append({"hole_no": block["hole_no"], "time": label, "opens": block["booking_opening_time"],
                        "min_members": block["min_members"], "max_players": block["max_players"]})
    out.sort(key=lambda c: (abs(minutes(c["time"]) - minutes(target)), holes.index(c["hole_no"])))
    return out


def opening_moment(day: dict, candidates: list) -> datetime:
    opens = parse_time(candidates[0]["opens"])
    d = datetime.strptime(day["booking_opening_day"], "%d-%m-%Y")
    return datetime(d.year, d.month, d.day, opens.hour, opens.minute, tzinfo=IST)


def server_clock_offset(client: TngfClient) -> float:
    """Seconds to add to this computer's clock to match the site's clock."""
    before = time.time()
    resp = client.http.head(BOOKING, timeout=TIMEOUT)
    after = time.time()
    server = parsedate_to_datetime(resp.headers["Date"]).timestamp()
    # The Date header has whole-second precision, so this is good to about a second.
    return server + 0.5 - (before + after) / 2


def sleep_until(moment_ts: float, offset: float) -> None:
    while True:
        remaining = moment_ts - (time.time() + offset)
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30) if remaining > 1 else remaining)


class HoldRequests:
    """Sends hold requests from several threads, each on its own open connection."""

    def __init__(self, client: TngfClient, size: int) -> None:
        self.client = client
        self.headers = client.api_headers()
        self.idle = queue.Queue()
        for _ in range(size):
            self.idle.put(self._connection())

    def _connection(self) -> requests.Session:
        http = requests.Session()
        http.headers.update(self.client.http.headers)
        http.cookies.update(self.client.http.cookies)
        return http

    def warm_up(self) -> int:
        """Connect now, so the first real request does not pay for the handshake."""
        connections = []
        while not self.idle.empty():
            connections.append(self.idle.get())
        ready = []

        def connect(http: requests.Session) -> None:
            try:
                http.head(BOOKING, timeout=TIMEOUT)
                ready.append(http)
            except requests.RequestException:
                pass

        threads = [threading.Thread(target=connect, args=(http,), daemon=True) for http in connections]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT)
        for http in connections:
            self.idle.put(http)
        return len(ready)

    def hold(self, date_ddmmyyyy: str, slot: dict) -> Reply:
        try:
            http = self.idle.get_nowait()
        except queue.Empty:
            http = self._connection()
        try:
            resp = http.post(
                urljoin(BOOKING, "api/teetime/slots/"),
                json={"date": date_ddmmyyyy, "hole_no": slot["hole_no"], "time": slot["time"], "status": "Blocked"},
                headers=self.headers, timeout=HOLD_TIMEOUT,
            )
        except requests.RequestException as err:
            return Reply(ERROR, detail=type(err).__name__)
        self.idle.put(http)
        try:
            body = resp.json()
        except ValueError:
            body = {}
        message = str(body.get("message", "")) if isinstance(body, dict) else ""
        if resp.status_code in (200, 201) and isinstance(body, dict) and body.get("url"):
            return Reply(HELD, url=body["url"])
        if resp.status_code in (401, 403):
            return Reply(SESSION, detail=f"HTTP {resp.status_code}")
        if message in ("Blocked", "Booked"):
            return Reply(TAKEN, detail=message)
        if "not open" in message.lower():
            return Reply(NOT_OPEN)
        return Reply(OTHER, detail=f"HTTP {resp.status_code} {resp.text[:160]!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", default=next_sunday(), help="DD-MM-YYYY (default: next Sunday)")
    parser.add_argument("--session", default="Morning", choices=["Morning", "Afternoon"])
    parser.add_argument("--target", default="", help='preferred tee time, e.g. "06:30 AM"; nearest slots are tried first')
    parser.add_argument("--order", default="", help='exact times to try, in order, e.g. "06:18 AM,06:25 AM"; '
                                                    "only these times are tried")
    parser.add_argument("--earliest", default="", help='do not book before this, e.g. "06:04 AM"')
    parser.add_argument("--latest", default="", help='do not book after this, e.g. "07:15 AM"')
    parser.add_argument("--holes", default="1,10", help="starting holes in order of preference")
    parser.add_argument("--holes-first", action="store_true",
                        help="with --order: try every time on the first hole before any time on the next hole")
    parser.add_argument("--wait", action="store_true", help="if the window is not open yet, wait for it")
    parser.add_argument("--live", action="store_true", help="really hold and book (default is a rehearsal)")
    parser.add_argument("--include-taken", action="store_true",
                        help="also try slots the site lists as taken, in case one has been released")
    parser.add_argument("--stagger", type=float, default=0.25,
                        help="seconds between starting on one choice and the next (default 0.25)")
    parser.add_argument("--grace", type=float, default=1.0,
                        help="after a hold, seconds to wait for a better choice still in flight (default 1)")
    args = parser.parse_args()
    holes = [int(h) for h in args.holes.split(",")]
    order = [minutes(t) for t in args.order.split(",") if t.strip()]
    if bool(order) == bool(args.target):
        parser.error("give either --target or --order")
    for label in filter(None, [args.target, args.earliest, args.latest]):
        parse_time(label)

    load_dotenv()
    member_id = os.environ.get("TNGF_MEMBER_ID", "").strip()
    password = os.environ.get("TNGF_PASSWORD", "")
    partners = [p.strip().upper() for p in os.environ.get("TNGF_PLAYERS", "").split(",") if p.strip()]
    if not member_id or not password:
        print("Fill in TNGF_MEMBER_ID and TNGF_PASSWORD in .env first.", file=sys.stderr)
        return 2

    client = TngfClient()
    try:
        client.login(member_id, password)
        day = (client.slots_for_date(args.date).get("data")) or {}
        candidates = pick_candidates(day, args.session, args.target or "12:00 PM", args.earliest, args.latest,
                                     holes, args.include_taken)
        if order:
            candidates = [c for c in candidates if minutes(c["time"]) in order]
            if args.holes_first:
                candidates.sort(key=lambda c: (holes.index(c["hole_no"]), order.index(minutes(c["time"]))))
            else:
                candidates.sort(key=lambda c: (order.index(minutes(c["time"])), holes.index(c["hole_no"])))
        if not candidates:
            print("No free member slots match those preferences.", file=sys.stderr)
            return 1

        by_username = {str(m.get("username", "")).upper(): m for m in client.members()}
        missing = [p for p in partners if p not in by_username]
        if missing:
            print(f"Not in the member list: {', '.join(missing)}", file=sys.stderr)
            return 1
        player_ids = [client.user["id"]] + [by_username[p]["id"] for p in partners]
        need = candidates[0]["min_members"]
        if not need <= len(player_ids) <= candidates[0]["max_players"]:
            print(f"These slots need {need} to {candidates[0]['max_players']} members; "
                  f"you have {len(player_ids)}. Check TNGF_PLAYERS in .env.", file=sys.stderr)
            return 1

        opens_at = opening_moment(day, candidates)
        offset = server_clock_offset(client)
        now = datetime.fromtimestamp(time.time() + offset, IST)
        print(f"Date {args.date}, {args.session.lower()} session, {len(player_ids)} players "
              f"({member_id.upper()}, {', '.join(partners)}).")
        print(f"Window opens {opens_at:%a %d %b %Y %I:%M %p} IST; site time is now {now:%a %d %b %I:%M:%S %p}.")
        print("Order of attempts: " + ", ".join(f"{c['time']} hole {c['hole_no']}" for c in candidates[:12])
              + (" ..." if len(candidates) > 12 else ""))

        if not args.live:
            print("\nRehearsal only: nothing was held or booked. Add --live to book.")
            return 0

        if now < opens_at:
            if not args.wait:
                print("\nThe window is not open yet. Add --wait to wait for it.", file=sys.stderr)
                return 1
            open_ts = opens_at.timestamp()
            if open_ts - (time.time() + offset) > LOGIN_LEAD + 30:
                print(f"Waiting; will log in again {LOGIN_LEAD} seconds before the window opens.")
                sleep_until(open_ts - LOGIN_LEAD, offset)
                client = TngfClient()
                client.login(member_id, password)
                offset = server_clock_offset(client)
            print("Logged in and ready. Waiting for the window to open...")
            sleep_until(open_ts - WARM_LEAD, offset)

        print_lock = threading.Lock()

        def log(line: str) -> None:
            stamp = datetime.fromtimestamp(time.time() + offset, IST).strftime("%H:%M:%S.%f")[:-3]
            with print_lock:
                print(f"[{stamp}] {line}")

        slots = list(candidates)
        logins_left = 1
        while True:
            requests_out = HoldRequests(client, min(len(slots) * 2, 12))
            log(f"{requests_out.warm_up()} connections open.")
            # Times below are on this computer's clock; the site's clock is `offset` ahead.
            open_local = max(opens_at.timestamp() - offset, time.time())
            race = Race(slots, lambda slot: requests_out.hold(args.date, slot), log,
                        open_at=open_local, deadline=open_local + TRY_FOR,
                        stagger=args.stagger, grace=args.grace, max_inflight=MAX_INFLIGHT)
            outcome = race.run()
            if outcome.reason != "session":
                break
            # A login anywhere else (browser or another run) ends this session.
            race.drain(HOLD_TIMEOUT[1] + HOLD_TIMEOUT[0])
            if logins_left == 0:
                print("Session ended again. Something else is logging in with this membership. Stopping.",
                      file=sys.stderr)
                return 1
            logins_left -= 1
            log("Session ended; logging in again.")
            client = TngfClient()
            client.login(member_id, password)
            slots = [state.slot for state in outcome.states if not state.lost]
            if not slots:
                break

        booked = None
        refused = False
        if outcome.best:
            slot, url = outcome.best.slot, outcome.best.urls[0]
            log(f"Holding {slot['time']} hole {slot['hole_no']}. Confirming...")
            done = client.confirm_slot(url, player_ids)
            if done.status_code in (200, 202):
                # The site answers 200 with the slot. Check our booking list before
                # claiming it, and never release a slot the site accepted.
                booked = url
                slot_id = url.rstrip("/").split("/")[-1]
                try:
                    listed = any(str(b.get("url", "")).rstrip("/").split("/")[-1] == slot_id
                                 for b in client.my_bookings(args.date))
                except TngfError:
                    listed = False
                if listed:
                    print(f"BOOKED: {args.date} {slot['time']}, hole {slot['hole_no']}.")
                else:
                    print(f"The site accepted the booking for {slot['time']} hole {slot['hole_no']} "
                          f"(HTTP {done.status_code}) but it is not in the booking list yet. "
                          "Check the site; the slot was NOT released.", file=sys.stderr)
            else:
                refused = True
                print(f"Confirm was refused (HTTP {done.status_code}): {done.text[:300]}", file=sys.stderr)

        # Give back everything else we are holding, including holds that answer late.
        for url in race.drain(HOLD_TIMEOUT[1] + HOLD_TIMEOUT[0]):
            if url != booked:
                released = client.release_slot(url)
                log(f"Released a held slot we are not using (HTTP {released.status_code}).")
        if any(state.inflight for state in outcome.states):
            print("Some requests never answered. If one of them held a slot, the site frees it "
                  "by itself when the hold times out.", file=sys.stderr)

        if booked:
            return 0
        if refused:
            print("Stopping so nothing is retried blindly.", file=sys.stderr)
            return 1
        reasons = {"all_taken": "all preferred slots were taken.", "timeout": "ran out of time.",
                   "session": "the session ended and no slot was left to try."}
        print("Did not get a slot: " + reasons.get(outcome.reason, outcome.reason), file=sys.stderr)
        return 1
    except TngfError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1
    finally:
        closed = client.logout()
        if closed:
            print("Logged out: " + ", ".join(closed))


if __name__ == "__main__":
    sys.exit(main())
