---
name: amy-tee-bookings
description: Show the tee times currently booked with the user's TNGF membership, read from the club site. Use when the user says "my bookings", "current bookings", "what have I booked", "do I have a tee time", "did it book", "show past bookings", or types /amy-tee-bookings.
---

# Tee-time bookings at TNGF

Read-only: this never books, changes or cancels anything.

## Current bookings ("my bookings", "do I have a tee time", `/amy-tee-bookings`)

1. From the project folder run `.venv/bin/python -W ignore -m teebot.bookings`. It logs in, reads the booking list and logs out.
2. Report the upcoming bookings as printed: day and date, time, hole, session, players, and who booked it. If there are none, say so plainly.

## Past bookings ("booking history", "past bookings", `/amy-tee-bookings all`)

Run the same command with `--all`. Past bookings are listed newest first.

## Rules

- Never run this while a booking run is going or in the minutes before a booking window opens. The site allows one session per membership, and a second login ends the booking run. The command refuses by itself; do not work around it.
- The list covers bookings that include the user as a player, whoever made them.
- If the command fails, show its message and stop.
