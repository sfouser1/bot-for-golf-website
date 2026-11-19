# TNGF booking schedule

Given by the user on 2026-11-19. All times are IST. The booking window for a playing day opens on the booking day at the time shown.

| Playing | Booking opens | Reminder goes out (day before booking) |
|---|---|---|
| Tue | Sun 12:00 PM | Sat |
| Thu | Tue 12:00 PM | Mon |
| Sat, morning | Wed 08:45 AM | Tue |
| Sat, afternoon | Wed 09:00 AM | Tue |
| Sun, morning | Thu 08:45 AM | Wed |
| Sun, afternoon | Thu 09:00 AM | Wed |

Notes:

- The Sunday rows match what the site showed on 2026-11-19 (morning blocks open 08:45 AM, afternoon blocks 09:00 AM). The other rows are from the user's table and have not been checked against the site.
- `book-sunday.sh` covers Sunday morning only. The other playing days need their own date, session and times passed to `teebot.book`.
