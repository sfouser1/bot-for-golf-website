---
name: amy-tee-group
description: Browse the user's history of preferred groups at TNGF, pick players from it, and set a new preferred group on the club site. Use when the user says "show my group", "group history", "who is in my group", "set group", "change my group", "set preferred group", or types /amy-tee-group.
---

# Preferred group at TNGF

All commands run from the project folder with `.venv/bin/python -W ignore -m teebot.group ...`.
Every group read from the site is saved in `saved-groups/` with a timestamp; that folder is the history.

## Browse ("group history", "show my group", `/amy-tee-group`)

1. Run `... -m teebot.group history`. It needs no login. It prints the saved groups, numbered with the most recent first, and every player seen in them.
2. Show the user that list as printed. If they also want what is on the site right now, run `... -m teebot.group show` (this logs in and out).

## Set a new group ("set group to ...", "use group 2", "swap Ravi for Babu")

1. If the user has not said exactly who, show the history first and let them pick: a numbered group, or individual players from the list. A player not in the history can still be named by membership ID or name.
2. Run one of:
   - `... -m teebot.group set --group N` to reuse a numbered group from the history.
   - `... -m teebot.group set ID_OR_NAME ID_OR_NAME [ID_OR_NAME]` for chosen players. Leave the user out; they are always in the group. Give 1 to 3 other players.
3. Report the `SET:` line exactly as printed. Do not claim the group changed unless that line is there. "nothing changed" means it was already that group.
4. If a name matches several members, the command lists them and stops. Show the list and ask which one.

## The Sunday booking is separate

The booking bot takes its players from `TNGF_PLAYERS` in `.env`, not from the group on the site. After every `set`, the command prints who the Sunday booking will use. Tell the user that line.

Add `--update-bot` to the `set` command only when the user asks for the Sunday booking to use the new group too. It needs at least 2 other players for a member slot, so rehearse afterwards (`./book-sunday.sh --rehearse`) and report the plan.

## Rules

- Never run `show` or `set` while a booking run is going. The site allows one session per membership, and a second login ends the booking run. The command refuses by itself; do not work around it.
- Do not run these in the minutes before a booking window opens.
- If a command fails, show its message and stop.
