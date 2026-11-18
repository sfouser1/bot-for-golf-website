"""Look up members by membership ID or name, to get the IDs the booking call needs.

Usage:
    .venv/bin/python -m teebot.find_member AB123 "Some Name"
"""
import os
import sys

from dotenv import load_dotenv

from .client import TngfClient, TngfError


def norm(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def main() -> int:
    queries = sys.argv[1:]
    if not queries:
        print(__doc__, file=sys.stderr)
        return 2
    load_dotenv()
    client = TngfClient()
    try:
        client.login(os.environ.get("TNGF_MEMBER_ID", "").strip(), os.environ.get("TNGF_PASSWORD", ""))
        members = client.members()
    except TngfError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1
    finally:
        closed = client.logout()
        if closed:
            print("Logged out: " + ", ".join(closed))

    print(f"{len(members)} members in the player list.")
    for query in queries:
        q = norm(query)
        hits = [
            m for m in members
            if norm(m.get("username", "")) == q
            or q in norm(f"{m.get('first_name', '')}{m.get('last_name', '')}")
            or q in norm(f"{m.get('last_name', '')}{m.get('first_name', '')}")
        ]
        print(f"\n{query!r}: {len(hits)} match(es)")
        for m in hits[:15]:
            print(f"  {m.get('username', '?'):10} id={m.get('id')}  {m.get('first_name', '')} {m.get('last_name', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
