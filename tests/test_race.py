"""Simulated races: no network, no site. Run with
    .venv/bin/python -m unittest discover -s tests
"""
import threading
import time
import unittest

from teebot.race import ERROR, HELD, NOT_OPEN, OTHER, SESSION, TAKEN, Race, Reply

SLOTS = [{"time": t, "hole_no": 1} for t in ("06:18 AM", "06:25 AM", "06:39 AM", "06:32 AM")]


class FakeSite:
    """Answers hold requests from a script: per slot, a list of (delay, kind) in call order."""

    def __init__(self, script: dict, default=(0.01, TAKEN)) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.default = default
        self.calls = []
        self.lock = threading.Lock()

    def hold(self, slot: dict) -> Reply:
        with self.lock:
            self.calls.append(slot["time"])
            steps = self.script.get(slot["time"], [])
            delay, kind = steps.pop(0) if steps else self.default
        time.sleep(delay)
        return Reply(kind, url=f"slot/{slot['time']}/" if kind == HELD else "")


def run(site: FakeSite, **options):
    settings = dict(stagger=0.02, early=0.0, grace=0.3, hedge_after=0.15, poll_gap=0.02, other_gap=0.05)
    settings.update(options)
    now = time.time()
    race = Race(SLOTS, site.hold, lambda line: None, open_at=now, deadline=now + 2.0, **settings)
    outcome = race.run()
    return race, outcome


class RaceTest(unittest.TestCase):
    def test_first_choice_wins_when_free(self):
        race, outcome = run(FakeSite({"06:18 AM": [(0.01, HELD)]}))
        self.assertEqual(outcome.best.slot["time"], "06:18 AM")
        self.assertEqual(race.drain(1), ["slot/06:18 AM/"])

    def test_slow_first_choice_does_not_block_the_others(self):
        # Thursday's failure: the first choice answers slowly; the second is free.
        site = FakeSite({"06:18 AM": [(1.0, TAKEN)] * 3, "06:25 AM": [(0.02, HELD)]})
        started = time.time()
        race, outcome = run(site)
        self.assertEqual(outcome.best.slot["time"], "06:25 AM")
        self.assertLess(time.time() - started, 0.6)

    def test_waits_briefly_for_a_better_choice_in_flight(self):
        site = FakeSite({"06:18 AM": [(0.2, HELD)], "06:25 AM": [(0.02, HELD)]}, default=(0.5, TAKEN))
        race, outcome = run(site)
        self.assertEqual(outcome.best.slot["time"], "06:18 AM")
        self.assertEqual(sorted(race.drain(1)), ["slot/06:18 AM/", "slot/06:25 AM/"])

    def test_does_not_wait_past_the_grace_time(self):
        site = FakeSite({"06:18 AM": [(1.0, HELD)] * 3, "06:25 AM": [(0.02, HELD)]}, default=(0.5, TAKEN))
        race, outcome = run(site, max_inflight=1)
        self.assertEqual(outcome.best.slot["time"], "06:25 AM")
        # The late hold on the first choice still comes back, so it can be released.
        self.assertIn("slot/06:18 AM/", race.drain(2))

    def test_window_not_open_then_opens(self):
        site = FakeSite({"06:18 AM": [(0.01, NOT_OPEN)] * 5 + [(0.01, HELD)]}, default=(0.01, NOT_OPEN))
        race, outcome = run(site)
        self.assertEqual(outcome.best.slot["time"], "06:18 AM")
        # While closed, the other choices stay quiet after their first answer.
        self.assertLessEqual(site.calls.count("06:25 AM"), 2)

    def test_all_taken(self):
        race, outcome = run(FakeSite({}))
        self.assertIsNone(outcome.best)
        self.assertEqual(outcome.reason, "all_taken")
        self.assertEqual(race.drain(1), [])

    def test_taken_reply_before_our_own_slow_success(self):
        # Our first request took the slot but answers late; the second request for the
        # same slot sees it as taken. The slot must still count as ours.
        site = FakeSite({"06:18 AM": [(0.4, HELD), (0.01, TAKEN)]}, default=(0.6, TAKEN))
        race, outcome = run(site, hedge_after=0.05)
        self.assertEqual(outcome.best.slot["time"], "06:18 AM")

    def test_session_ended_stops_the_race(self):
        race, outcome = run(FakeSite({"06:18 AM": [(0.01, SESSION)]}, default=(0.3, TAKEN)))
        self.assertEqual(outcome.reason, "session")

    def test_errors_and_odd_replies_are_retried_until_time_runs_out(self):
        site = FakeSite({}, default=(0.01, OTHER))
        now = time.time()
        race = Race(SLOTS[:1], site.hold, lambda line: None, open_at=now, deadline=now + 0.3,
                    early=0.0, other_gap=0.05)
        self.assertEqual(race.run().reason, "timeout")
        self.assertGreater(len(site.calls), 2)
        self.assertLess(len(site.calls), 10)

    def test_a_crashing_request_is_reported_not_lost(self):
        def hold(slot):
            raise RuntimeError("boom")
        now = time.time()
        lines = []
        race = Race(SLOTS[:1], hold, lines.append, open_at=now, deadline=now + 0.15, early=0.0, other_gap=0.05)
        self.assertEqual(race.run().reason, "timeout")
        self.assertTrue(any(ERROR in line and "boom" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
