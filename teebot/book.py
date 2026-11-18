"""Book a tee slot for your group.

Without --live this is a rehearsal: it logs in, works out which slots it would
try and in what order, and stops. With --live it holds and books one slot.

Usage:
    .venv/bin/python -m teebot.book --target "06:30 AM"                 # rehearsal, next Sunday
    .venv/bin/python -m teebot.book --target "06:30 AM" --live --wait   # wait for the window, then book
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

from dotenv import load_dotenv

from .client import BOOKING, TIMEOUT, TngfClient, TngfError
from .list_slots import IST, next_sunday

LOGIN_LEAD = 120      # log in this many seconds before the window opens
TRY_FOR = 30          # keep trying this many seconds after the window opens
RETRY_GAP = 0.25      # pause between attempts while the window is not open yet


def parse_time(label: str) -> datetime:
    return datetime.strptime(label.strip().upper(), "%I:%M %p")


def minutes(label: str) -> int:
    t = parse_time(label)
    return t.hour * 60 + t.minute


def pick_candidates(day: dict, session: str, target: str, earliest: str, latest: str, holes: list) -> list:
    """Free member slots in the window, closest to the target first."""
    free = {str(h): {e["slot"] for e in entries} for h, entries in (day.get("available_slots") or {}).items()}
    out = []
    for block in (day.get("templates") or {}).get("template_details") or []:
        if block["guest_only"] or block["session_name"].lower() != session.lower():
            continue
        if block["hole_no"] not in holes:
            continue
        for label in block["slots"]:
            if label not in free.get(str(block["hole_no"]), set()):
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", default=next_sunday(), help="DD-MM-YYYY (default: next Sunday)")
    parser.add_argument("--session", default="Morning", choices=["Morning", "Afternoon"])
    parser.add_argument("--target", required=True, help='preferred tee time, e.g. "06:30 AM"')
    parser.add_argument("--earliest", default="", help='do not book before this, e.g. "06:04 AM"')
    parser.add_argument("--latest", default="", help='do not book after this, e.g. "07:15 AM"')
    parser.add_argument("--holes", default="1,10", help="starting holes in order of preference")
    parser.add_argument("--wait", action="store_true", help="if the window is not open yet, wait for it")
    parser.add_argument("--live", action="store_true", help="really hold and book (default is a rehearsal)")
    args = parser.parse_args()
    holes = [int(h) for h in args.holes.split(",")]
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
        candidates = pick_candidates(day, args.session, args.target, args.earliest, args.latest, holes)
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
            sleep_until(open_ts, offset)

        deadline = max(time.time() + offset, opens_at.timestamp()) + TRY_FOR
        queue = list(candidates)
        relogins_left = 1
        while queue and time.time() + offset < deadline:
            slot = queue[0]
            resp = client.hold_slot(args.date, slot["hole_no"], slot["time"])
            try:
                body = resp.json()
            except ValueError:
                body = {}
            stamp = datetime.fromtimestamp(time.time() + offset, IST).strftime("%H:%M:%S")
            if resp.status_code in (200, 201) and body.get("url"):
                print(f"[{stamp}] Holding {slot['time']} hole {slot['hole_no']}. Confirming...")
                done = client.confirm_slot(body["url"], player_ids)
                if done.status_code == 202:
                    print(f"BOOKED: {args.date} {slot['time']}, hole {slot['hole_no']}.")
                    return 0
                print(f"Confirm was refused (HTTP {done.status_code}): {done.text[:300]}", file=sys.stderr)
                released = client.release_slot(body["url"])
                print(f"Released the held slot (HTTP {released.status_code}). Stopping so nothing is retried blindly.",
                      file=sys.stderr)
                return 1
            if resp.status_code in (401, 403):
                # A login anywhere else (browser or another run) ends this session.
                if relogins_left == 0:
                    print(f"[{stamp}] Session ended again (HTTP {resp.status_code}). Something else is "
                          "logging in with this membership. Stopping.", file=sys.stderr)
                    return 1
                relogins_left -= 1
                print(f"[{stamp}] Session ended (HTTP {resp.status_code}); logging in again.")
                client = TngfClient()
                client.login(member_id, password)
                continue
            message = str(body.get("message", "")) if isinstance(body, dict) else ""
            if message in ("Blocked", "Booked"):
                print(f"[{stamp}] {slot['time']} hole {slot['hole_no']} already taken; trying the next one.")
                queue.pop(0)
                continue
            print(f"[{stamp}] {slot['time']} hole {slot['hole_no']}: HTTP {resp.status_code} {resp.text[:160]!r}; retrying.")
            time.sleep(RETRY_GAP)

        print("Did not get a slot: " + ("all preferred slots were taken." if not queue else "ran out of time."),
              file=sys.stderr)
        return 1
    except TngfError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
