---
name: amy-book-tee
description: Book next Sunday's tee time at TNGF with the saved preferences. Use when the user says "book tee time", "book my tee time", "book Sunday", or types /amy-book-tee. Also handles "rehearse tee time" (a dry run that books nothing).
---

# Book the Sunday tee time

The user's phrase is the instruction to run the booking; do not ask for confirmation first.

## Live booking ("book tee time", `/amy-book-tee`)

1. Run `./book-sunday.sh` from the project folder **in the background**, because it may wait longer than a foreground command is allowed to run. The script logs in by itself, waits for the booking window using the site's clock, books one slot, and logs out. It keeps the Mac awake while it runs.
2. Straight after, start a Monitor with the command `./watch-run.sh` (run from the project folder) and `timeout_ms` 1800000. Every line the run prints arrives as an event.
3. Tell the user it has started and what it printed so far: the date, the players, when the window opens, and the order of slots it will try.
4. Give real-time status: each time an event arrives, post one short line to the user saying what just happened, in plain words with the time. Events to expect, in order: a "Still waiting" line every ten minutes, "logging in again" two minutes before the window, "Logged in and ready", "connections open" ten seconds before, then one line per slot answer (`held`, `taken`), "Confirming...", and the result. Several lines that arrive together get one combined update.
5. If the Monitor expires while the run is still going, start it again with `./watch-run.sh new` so earlier lines are not repeated. Do this without comment.
6. The event `RUN ENDED` means the run has exited. Report the result exactly as printed: the `BOOKED:` line with date, time and hole, or the reason it stopped. Do not claim a booking unless the `BOOKED:` line is there.
7. Remind the user once, when starting: stay logged out of the club website while it runs, because a login elsewhere ends the script's session.

## Rehearsal ("rehearse tee time", `/amy-book-tee rehearse`)

Run `./book-sunday.sh --rehearse` in the foreground and report the plan it prints. Nothing is held or booked.

## Rules

- Never start a second run while one is still going; the site allows one session per membership, and two runs can book two slots.
- Do not change the preferences in `book-sunday.sh` unless the user asks.
- If the script fails, show its message and stop. Do not retry a live booking on your own.
