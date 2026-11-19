"""Browse and set the preferred group saved on the club's booking site.

Every group seen on the site is recorded in saved-groups/ with a timestamp, so
earlier groups and their players can be picked again later.

Usage:
    .venv/bin/python -m teebot.group history            # saved groups and players (no login)
    .venv/bin/python -m teebot.group show               # the group on the site right now
    .venv/bin/python -m teebot.group set PM914 SC087    # replace the group on the site
    .venv/bin/python -m teebot.group set --group 2      # reuse group 2 from the history list

Players are given as membership IDs or names, without yourself; you are always
in the group. `set` changes the site only. Add --update-bot to also make the
Sunday booking use the same players (TNGF_PLAYERS in .env).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from dotenv import load_dotenv

from .client import BOOKING, TIMEOUT, TngfClient, TngfError
from .find_member import norm
from .list_slots import IST

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "saved-groups"
ENV_FILE = ROOT / ".env"
GROUP_API = urljoin(BOOKING, "api/teetime/preferredgroup/")
# Resource URLs inside the site's replies use this base (see docs/site-flow.md).
MEMBER_URL = "https://63v082fjye.execute-api.ap-south-1.amazonaws.com/production/api/member/{id}/"
MAX_PLAYERS = 4


# ---- history on disk ----------------------------------------------------

def person(entry: dict) -> dict:
    """Keep only what is needed to pick a player again."""
    name = entry.get("name") or f"{entry.get('first_name', '')} {entry.get('last_name', '')}".strip()
    return {"id": entry["id"], "username": str(entry.get("username", "")), "name": name}


def load_history() -> list:
    """Saved snapshots, oldest first."""
    snapshots = []
    for path in sorted(HISTORY_DIR.glob("preferred-group-*.json")):
        try:
            snapshots.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            print(f"Skipping unreadable file {path.name}", file=sys.stderr)
    return snapshots


def distinct_groups(snapshots: list) -> list:
    """One entry per distinct set of players, most recently seen first."""
    latest = {}
    for snap in snapshots:
        latest[frozenset(snap.get("players", []))] = snap
    return sorted(latest.values(), key=lambda s: s.get("saved_at", ""), reverse=True)


def known_players(snapshots: list) -> list:
    people = {}
    for snap in snapshots:
        for p in snap.get("players_list", []):
            people[p["id"]] = p
    return sorted(people.values(), key=lambda p: p["name"].lower())


def record(group: dict, note: str) -> Optional[Path]:
    """Save a group read from the site, unless it is the same as the latest snapshot."""
    snapshots = load_history()
    if snapshots and set(snapshots[-1].get("players", [])) == set(group.get("players", [])):
        return None
    now = datetime.now(IST)
    HISTORY_DIR.mkdir(exist_ok=True)
    path = HISTORY_DIR / f"preferred-group-{now:%Y-%m-%d-%H%M%S}-IST.json"
    path.write_text(json.dumps({
        "saved_at": f"{now:%Y-%m-%d %H:%M:%S} IST",
        "note": note,
        "group_url": group.get("url"),
        "member": group.get("member"),
        "players": group.get("players", []),
        "players_list": [person(p) for p in group.get("players_list", [])],
    }, indent=2) + "\n", encoding="utf-8")
    return path


def describe(players_list: list, me: str = "") -> str:
    return ", ".join(
        f"{p['name']} ({p['username']})" + (" [you]" if p["username"].upper() == me else "")
        for p in players_list
    )


# ---- the site -------------------------------------------------------------

def booking_run_active() -> bool:
    """True if a booking run is going; a second login would end its session."""
    found = subprocess.run(["pgrep", "-f", "teebot.book"], capture_output=True, text=True)
    return bool(found.stdout.strip())


def read_group(client: TngfClient) -> Optional[dict]:
    resp = client.http.get(GROUP_API, headers=client.api_headers(), timeout=TIMEOUT)
    if resp.status_code != 200:
        raise TngfError(f"Could not read the preferred group: HTTP {resp.status_code}")
    groups = resp.json()
    return groups[0] if groups else None


def write_group(client: TngfClient, current: Optional[dict], player_ids: list) -> None:
    member = MEMBER_URL.format(id=client.user["id"])
    if current:
        group_id = current["url"].rstrip("/").split("/")[-1]
        resp = client.http.patch(
            urljoin(GROUP_API, f"{group_id}/"),
            json={"member": current.get("member") or member, "players": player_ids},
            headers=client.api_headers(), timeout=TIMEOUT,
        )
    else:
        # Never exercised: the account had a group already when this was written.
        resp = client.http.post(
            GROUP_API, json={"member": member, "players": player_ids},
            headers=client.api_headers(), timeout=TIMEOUT,
        )
    if resp.status_code not in (200, 201, 202):
        raise TngfError(f"The site refused the new group: HTTP {resp.status_code} {resp.text[:200]}")


def find_player(query: str, people: list) -> list:
    """People whose membership ID equals the query, or whose name contains it."""
    q = norm(query)
    exact = [p for p in people if norm(p["username"]) == q]
    return exact or [p for p in people if q and q in norm(p["name"])]


def resolve(queries: list, snapshots: list, client: TngfClient) -> list:
    """Turn membership IDs or names into players: history first, then the member list."""
    history = known_players(snapshots)
    members = None
    chosen = []
    for query in queries:
        hits = find_player(query, history)
        if not hits:
            if members is None:
                members = [person(m) for m in client.members()]
            hits = find_player(query, members)
        if not hits:
            raise TngfError(f"No member matches {query!r}.")
        if len(hits) > 1:
            options = "; ".join(f"{p['name']} ({p['username']})" for p in hits[:8])
            raise TngfError(f"{query!r} matches {len(hits)} members, use the membership ID: {options}")
        chosen.append(hits[0])
    return chosen


def update_bot_players(usernames: list) -> None:
    """Point the Sunday booking at these partners (TNGF_PLAYERS in .env)."""
    line = "TNGF_PLAYERS=" + ",".join(usernames)
    text = ENV_FILE.read_text(encoding="utf-8")
    if re.search(r"^TNGF_PLAYERS=.*$", text, flags=re.M):
        text = re.sub(r"^TNGF_PLAYERS=.*$", line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    ENV_FILE.write_text(text, encoding="utf-8")


# ---- commands -------------------------------------------------------------

def cmd_history(me: str) -> int:
    snapshots = load_history()
    if not snapshots:
        print("No saved groups yet. Run `show` once to record the group on the site.")
        return 0
    print("Saved groups, most recent first:")
    for number, snap in enumerate(distinct_groups(snapshots), 1):
        print(f"  {number}. {describe(snap['players_list'], me)}   (seen {snap.get('saved_at', '?')})")
    print("\nPlayers in your history:")
    for p in known_players(snapshots):
        if p["username"].upper() != me:
            print(f"  {p['username']:14} {p['name']}")
    return 0


def cmd_site(args, me: str, password: str) -> int:
    if booking_run_active():
        print("A booking run is going. Logging in now would end its session; try again after it.",
              file=sys.stderr)
        return 1
    snapshots = load_history()
    queries = list(args.players)
    if args.command == "set" and args.group:
        groups = distinct_groups(snapshots)
        if not 1 <= args.group <= len(groups):
            print(f"There is no group {args.group} in the history (it has {len(groups)}).", file=sys.stderr)
            return 2
        queries += [p["username"] for p in groups[args.group - 1]["players_list"]]
    queries = [q for q in queries if norm(q) != norm(me)]

    client = TngfClient()
    try:
        client.login(me, password)
        current = read_group(client)
        if current:
            record(current, "Preferred group as found on the site.")
            print("On the site now: " + describe([person(p) for p in current["players_list"]], me))
        else:
            print("On the site now: no preferred group.")
        if args.command == "show":
            return 0

        partners = resolve(queries, snapshots, client)
        partner_ids = list(dict.fromkeys(p["id"] for p in partners if p["id"] != client.user["id"]))
        if not 1 <= len(partner_ids) <= MAX_PLAYERS - 1:
            print(f"Give 1 to {MAX_PLAYERS - 1} other players; got {len(partner_ids)}.", file=sys.stderr)
            return 2
        player_ids = [client.user["id"]] + partner_ids
        if current and set(current.get("players", [])) == set(player_ids):
            print("That is already the group on the site; nothing changed.")
        else:
            write_group(client, current, player_ids)
            after = read_group(client)
            if not after or set(after.get("players", [])) != set(player_ids):
                raise TngfError("The site accepted the change but still shows a different group.")
            record(after, "Preferred group after being set by teebot.group.")
            print("SET: " + describe([person(p) for p in after["players_list"]], me))
        if args.update_bot:
            by_id = {p["id"]: p["username"] for p in partners}
            update_bot_players([by_id[i] for i in partner_ids])
            print("Sunday booking now uses: " + ", ".join(by_id[i] for i in partner_ids))
        else:
            print("Sunday booking is unchanged: " + (os.environ.get("TNGF_PLAYERS", "") or "no players set"))
        return 0
    except TngfError as err:
        print(f"Stopped: {err}", file=sys.stderr)
        return 1
    finally:
        closed = client.logout()
        if closed:
            print("Logged out: " + ", ".join(closed))


def main() -> int:
    parser = argparse.ArgumentParser(description="Browse and set the preferred group on the club site.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("history", help="saved groups and players, no login")
    sub.add_parser("show", help="the group on the site right now")
    setter = sub.add_parser("set", help="replace the group on the site")
    setter.add_argument("players", nargs="*", help="membership IDs or names of the other players")
    setter.add_argument("--group", type=int, help="reuse this numbered group from `history`")
    setter.add_argument("--update-bot", action="store_true",
                        help="also make the Sunday booking use these players")
    args = parser.parse_args()
    if args.command == "set" and not args.players and not args.group:
        parser.error("give players or --group")
    if args.command != "set":
        args.players, args.group, args.update_bot = [], None, False

    load_dotenv(ENV_FILE)
    me = os.environ.get("TNGF_MEMBER_ID", "").strip().upper()
    password = os.environ.get("TNGF_PASSWORD", "")
    if args.command == "history":
        return cmd_history(me)
    if not me or not password:
        print("Fill in TNGF_MEMBER_ID and TNGF_PASSWORD in .env first.", file=sys.stderr)
        return 2
    return cmd_site(args, me, password)


if __name__ == "__main__":
    sys.exit(main())
