---
name: book-tee
description: Book next Sunday's tee time at TNGF with the saved preferences. Use when the user says "book tee time", "book my tee time", "book Sunday", or types /book-tee. Also handles "rehearse tee time" (a dry run that books nothing).
---

# Book the Sunday tee time

The user's phrase is the instruction to run the booking; do not ask for confirmation first.

## Live booking ("book tee time", `/book-tee`)

1. Run `./book-sunday.sh` from the project folder **in the background**, because it may wait longer than a foreground command is allowed to run. The script logs in by itself, waits for the booking window using the site's clock, books one slot, and logs out.
2. Tell the user it has started and what it printed so far: the date, the players, when the window opens, and the order of slots it will try.
3. When it exits, report the result exactly as printed: the `BOOKED:` line with date, time and hole, or the reason it stopped. Do not claim a booking unless the `BOOKED:` line is there.
4. Remind the user once, when starting: stay logged out of the club website while it runs, because a login elsewhere ends the script's session.

## Rehearsal ("rehearse tee time", `/book-tee rehearse`)

Run `./book-sunday.sh --rehearse` in the foreground and report the plan it prints. Nothing is held or booked.

## Rules

- Never start a second run while one is still going; the site allows one session per membership, and two runs can book two slots.
- Do not change the preferences in `book-sunday.sh` unless the user asks.
- If the script fails, show its message and stop. Do not retry a live booking on your own.
