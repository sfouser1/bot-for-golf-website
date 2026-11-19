---
name: amy-buggy
description: Buggy (golf cart) bookings at TNGF - see free buggies, book or cancel one, list the user's buggy bookings, and manage the weekly Friday-afternoon booking and the watcher that catches a cancelled buggy. Use when the user says "book a buggy", "buggy for Friday", "is a buggy free", "my buggy bookings", "did the buggy book", "cancel the buggy", "watch for a buggy", "buggy status", "stop the Friday buggy", or types /amy-buggy.
---

# Buggies at TNGF

Commands run from the project folder with `.venv/bin/python -W ignore -m teebot.buggy ...`.
The user usually plays Friday afternoon with their son and needs a buggy; defaults are Afternoon, hole 1, booked in the user's name.

## Look (`/amy-buggy`, "is a buggy free", "my buggy bookings", "buggy status")

With no other instruction, run these and report them together:

1. `./buggy-schedule.sh status` - whether the weekly Friday job and the watcher are scheduled, and their last log lines. Needs no login.
2. `... -m teebot.buggy mine` - the user's upcoming buggy bookings.
3. `... -m teebot.buggy show --date friday` if they ask what is free (any `DD-MM-YYYY` or weekday name works).

## Book one ("book a buggy for ...")

1. `... -m teebot.buggy book --date DATE --session Afternoon --hole 1 --live`. Without `--live` it is a rehearsal. Add `--wait` if the window has not opened; buggies open 7 days ahead at 06:00 AM, and close at 09:00 AM (morning) or 01:00 PM (afternoon) on the day.
2. Report the last line exactly: `BOOKED:`, `ALREADY BOOKED:`, `FULL:`, `CLOSED:` or `NOT BOOKED.` Do not claim a booking without a `BOOKED:` or `ALREADY BOOKED:` line.
3. If it says `FULL`, offer the watcher.

## Cancel ("cancel the buggy for ...")

`... -m teebot.buggy cancel --date DATE --session Afternoon --live`. Report the `CANCELLED:` line. The weekly job books every Friday, so the user must cancel the weeks they do not play.

## Scheduled jobs (`./buggy-schedule.sh`)

- `friday` - every Friday at 05:57 AM, book the Friday one week later (Afternoon, hole 1). `remove friday` stops it.
- `watch DD-MM-YYYY [Session]` - look every 55 to 85 minutes (never between 10:00 PM and 3:30 AM), book the first free buggy, and stop when booked or when the session closes. `remove watch` stops it. Only one watcher at a time.
- The jobs run from a copy of the bot in `~/.tngf-teebot`, because macOS does not let scheduled jobs read the Documents folder. After changing `teebot/`, `.env` or `buggy-schedule.sh`, run `./buggy-schedule.sh sync`.
- Logs: `~/.tngf-teebot/debug/buggy-friday.log` and `buggy-watch.log`.
- Each job ends by sending its result line as an iMessage to `NOTIFY_IMESSAGE_TO` in `.env`. A log ending `Message NOT sent` means macOS has not yet been allowed to let scheduled jobs use Messages (a one-time Allow on the Mac's screen; `./buggy-schedule.sh test-message` brings the prompt up). Until then `./buggy-schedule.sh relay`, left running from a terminal, sends what the jobs could not; check it with `pgrep -f "buggy-schedule.sh relay"`.

## Rules

- A buggy command and a tee-time booking run must not overlap: the site allows one session per membership. The commands refuse while `teebot.book` is running; do not work around it, and do not start a watcher that would be running during a tee-time booking window without telling the user it will be paused by hand (`remove watch`) first.
- The Mac must be awake for scheduled jobs. A job missed while asleep runs when the Mac wakes.
- If a command fails, show its message and stop. Do not retry a live booking on your own.
