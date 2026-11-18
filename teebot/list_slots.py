"""Log in and list the tee slots for a date. Read-only: nothing is held or booked.

Usage:
    .venv/bin/python -m teebot.list_slots                # next Sunday
    .venv/bin/python -m teebot.list_slots --date 22-11-2026
"""
import argparse
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .client import DEBUG_DIR, TngfClient, TngfError

IST = ZoneInfo("Asia/Kolkata")


def next_sunday() -> str:
    today = datetime.now(IST).date()
    days_ahead = (6 - today.weekday()) % 7 or 7
    return (today + timedelta(days=days_ahead)).strftime("%d-%m-%Y")


def print_summary(date: str, day: dict) -> None:
    """Print opening times and the free slots per session and starting hole."""
    print(f"\nSlots for {date}. Booking opens on {day.get('booking_opening_day', '?')}.")
    free = {
        str(hole): {entry["slot"] for entry in entries}
        for hole, entries in (day.get("available_slots") or {}).items()
    }
    details = (day.get("templates") or {}).get("template_details") or []
    for block in sorted(details, key=lambda b: (b["session"], b["hole_no"], b["guest_only"])):
        open_slots = [t for t in block["slots"] if t in free.get(str(block["hole_no"]), set())]
        kind = "guest-only (1 member + guests)" if block["guest_only"] else "members"
        print(
            f"\n{block['session_name']}, hole {block['hole_no']}, {kind}: "
            f"opens {block['booking_opening_time']}, "
            f"{block['min_players']}-{block['max_players']} players, "
            f"{block['min_members']}-{block['max_members']} members"
        )
        print(f"  free {len(open_slots)}/{len(block['slots'])}: {', '.join(open_slots) or 'none'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", default=next_sunday(), help="DD-MM-YYYY (default: next Sunday)")
    args = parser.parse_args()

    try:
        datetime.strptime(args.date, "%d-%m-%Y")
    except ValueError:
        print(f"Date must be DD-MM-YYYY, got {args.date!r}.", file=sys.stderr)
        return 2

    load_dotenv()
    member_id = os.environ.get("TNGF_MEMBER_ID", "").strip()
    password = os.environ.get("TNGF_PASSWORD", "")
    if not member_id or not password:
        print("Fill in TNGF_MEMBER_ID and TNGF_PASSWORD in .env first.", file=sys.stderr)
        return 2

    client = TngfClient()
    try:
        print("Logging in to the member portal...")
        landing = client.portal_login(member_id, password)
        print("Portal login accepted. Looking for the booking link...")
        handoff = client.find_handoff(landing)
        client.booking_login(handoff)
        print(f"Booking session opened (user type: {client.user.get('user_type', '?')}).")

        print(f"Loading slots for {args.date}...")
        data = client.slots_for_date(args.date)
    except TngfError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1
    finally:
        closed = client.logout()
        if closed:
            print("Logged out: " + ", ".join(closed))

    if data.get("error_msg") or data.get("unauthorized"):
        print(f"Stopped: site said {data.get('error_msg') or 'unauthorized'}", file=sys.stderr)
        return 1
    print_summary(args.date, data.get("data") or {})
    print(f"\nFull copy saved in {DEBUG_DIR.name}/slots_{args.date}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
