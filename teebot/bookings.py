"""List the tee times booked with your membership. Read-only.

Usage:
    .venv/bin/python -m teebot.bookings          # today and later
    .venv/bin/python -m teebot.bookings --all    # past bookings too, newest first
"""
import argparse
import os
import sys
from datetime import datetime
from urllib.parse import urljoin

from dotenv import load_dotenv

from .client import BOOKING, TIMEOUT, TngfClient, TngfError
from .group import ENV_FILE, booking_run_active
from .list_slots import IST


def fetch_bookings(client: TngfClient) -> list:
    """Every booking that includes the logged-in member, as the site's Booking List loads them."""
    found, page = [], 1
    while True:
        resp = client.http.get(
            urljoin(BOOKING, "api/teetime/slots/"),
            params={"players__id": client.user.get("id"), "status": "Booked", "sort_by": "date", "page": page},
            headers=client.api_headers(), timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise TngfError(f"Booking list failed: HTTP {resp.status_code}")
        data = resp.json()
        if not isinstance(data, dict):
            return found + list(data)
        found += data.get("results", [])
        if not data.get("next") or page >= int(data.get("total_pages") or 1):
            return found
        page += 1


def when(booking: dict) -> datetime:
    return datetime.strptime(f"{booking['date']} {booking['time']}", "%d-%m-%Y %I:%M %p")


def describe(booking: dict, me: str) -> str:
    players = ", ".join(
        f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() + f" ({p.get('username', '?')})"
        + (" [you]" if str(p.get("username", "")).upper() == me else "")
        for p in booking.get("players", [])
    )
    guests = booking.get("guests") or []
    booked_by = (booking.get("created_by") or {}).get("username", "?")
    return (f"{when(booking):%a %d %b %Y}, {booking['time']}, hole {booking['hole_no']} "
            f"({booking.get('session_name', '?')})\n"
            f"    players: {players}" + (f"; guests: {len(guests)}" if guests else "")
            + f"\n    booked by {booked_by}")


def main() -> int:
    parser = argparse.ArgumentParser(description="List the tee times booked with your membership.")
    parser.add_argument("--all", action="store_true", help="include past bookings, newest first")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    me = os.environ.get("TNGF_MEMBER_ID", "").strip().upper()
    password = os.environ.get("TNGF_PASSWORD", "")
    if not me or not password:
        print("Fill in TNGF_MEMBER_ID and TNGF_PASSWORD in .env first.", file=sys.stderr)
        return 2
    if booking_run_active():
        print("A booking run is going. Logging in now would end its session; try again after it.",
              file=sys.stderr)
        return 1

    client = TngfClient()
    try:
        client.login(me, password)
        bookings = fetch_bookings(client)
    except TngfError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1
    finally:
        closed = client.logout()
        if closed:
            print("Logged out: " + ", ".join(closed))

    today = datetime.now(IST).date()
    upcoming = sorted((b for b in bookings if when(b).date() >= today), key=when)
    past = sorted((b for b in bookings if when(b).date() < today), key=when, reverse=True)

    print(f"\nUpcoming bookings: {len(upcoming)}")
    for booking in upcoming:
        print("  " + describe(booking, me))
    if args.all:
        print(f"\nPast bookings: {len(past)}")
        for booking in past:
            print("  " + describe(booking, me))
    else:
        print(f"\n{len(past)} past booking(s) not shown; add --all to see them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
