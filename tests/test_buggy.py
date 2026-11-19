"""Buggy helpers: no network, no site. Run with
    .venv/bin/python -m unittest discover -s tests
"""
import random
import unittest
from datetime import date, datetime

from teebot.buggy import (cutoff_at, in_quiet_hours, next_look, opens_at, resolve_date,
                          session_state)
from teebot.list_slots import IST

CONF = {"session_name": "Afternoon", "buggies": 21, "booking_opening_day": -7,
        "booking_opening_time": "06:00 AM", "booking_cutoff_time": "01:00 PM"}


def row(status, who=1, session="Afternoon"):
    return {"session_name": session, "status": status, "booked_by_id": who, "url": "x/1/"}


class Dates(unittest.TestCase):
    def test_weekday_name_is_the_coming_one(self):
        self.assertEqual(resolve_date("friday", date(2026, 11, 19)), "20-11-2026")   # Thursday
        self.assertEqual(resolve_date("Friday", date(2026, 11, 20)), "27-11-2026")   # Friday: a week on
        self.assertEqual(resolve_date("friday", date(2026, 11, 21)), "27-11-2026")   # Saturday

    def test_plain_date_and_bad_date(self):
        self.assertEqual(resolve_date("20-11-2026", date(2026, 11, 19)), "20-11-2026")
        with self.assertRaises(ValueError):
            resolve_date("2026-11-20", date(2026, 11, 19))

    def test_window(self):
        self.assertEqual(opens_at(CONF, "27-11-2026"), datetime(2026, 11, 20, 6, 0, tzinfo=IST))
        self.assertEqual(cutoff_at(CONF, "27-11-2026"), datetime(2026, 11, 27, 13, 0, tzinfo=IST))


class Counts(unittest.TestCase):
    def test_cancelled_frees_a_buggy_and_not_available_holds_one(self):
        rows = [row("Booked", 5)] * 19 + [row("Not Available", None), row("Cancelled", 7),
                                          row("Booked", 9, "Morning")]
        state = session_state(CONF, rows, 7)
        self.assertEqual((state["taken"], state["free"], state["mine"]), (20, 1, []))

    def test_mine(self):
        state = session_state(CONF, [row("Booked", 7), row("Cancelled", 7)], "7")
        self.assertEqual(len(state["mine"]), 1)


class Pacing(unittest.TestCase):
    def test_quiet_hours(self):
        at = lambda h, m: datetime(2026, 11, 19, h, m, tzinfo=IST)
        self.assertTrue(in_quiet_hours(at(22, 0)))
        self.assertTrue(in_quiet_hours(at(3, 29)))
        self.assertFalse(in_quiet_hours(at(3, 30)))
        self.assertFalse(in_quiet_hours(at(21, 59)))

    def test_gaps_vary_and_skip_the_night(self):
        rng = random.Random(1)
        now = datetime(2026, 11, 19, 11, 0, tzinfo=IST)
        gaps = set()
        for _ in range(200):
            nxt = next_look(now, rng)
            self.assertFalse(in_quiet_hours(nxt), nxt)
            minutes = (nxt - now).total_seconds() / 60
            if minutes < 90:
                self.assertGreaterEqual(minutes, 55)
                gaps.add(round(minutes))
            now = nxt
        self.assertGreater(len(gaps), 10)

    def test_evening_look_moves_to_after_half_past_three(self):
        nxt = next_look(datetime(2026, 11, 19, 21, 30, tzinfo=IST), random.Random(2))
        self.assertEqual(nxt.date(), date(2026, 11, 20))
        self.assertTrue((3, 30) <= (nxt.hour, nxt.minute) <= (3, 42))


if __name__ == "__main__":
    unittest.main()
