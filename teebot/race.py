"""Race for one tee slot with overlapping requests.

Requests for the preferred slots are in flight at the same time, so one slow
reply from the site cannot hold up the others. The race ends with at most one
slot chosen; every other slot it managed to hold is handed back for release.

This module knows nothing about the site. `hold` is any function that takes a
slot and returns a Reply; it is called from several threads at once.
"""
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

HELD = "held"            # the slot is ours for now; Reply.url identifies it
TAKEN = "taken"          # someone else has it
NOT_OPEN = "not_open"    # the booking window has not opened yet
SESSION = "session"      # our login is no longer valid
OTHER = "other"          # anything else the site said
ERROR = "error"          # no usable answer (timeout, connection problem)


@dataclass
class Reply:
    kind: str
    url: str = ""
    detail: str = ""


@dataclass
class SlotState:
    slot: dict
    rank: int
    start: float                       # do not ask for this slot before this moment
    inflight: int = 0
    last_launch: float = 0.0
    gap: float = 0.0                   # pause before the next request when none is in flight
    taken_seen: bool = False
    urls: List[str] = field(default_factory=list)

    @property
    def lost(self) -> bool:
        # Final only once every request has answered: an earlier request of ours
        # may be the one that took the slot.
        return self.taken_seen and self.inflight == 0 and not self.urls


@dataclass
class Outcome:
    best: Optional[SlotState]          # the slot to confirm, or None
    reason: str                        # "held", "all_taken", "timeout" or "session"
    states: List[SlotState]


class Race:
    def __init__(self, slots: list, hold: Callable[[dict], Reply], log: Callable[[str], None],
                 open_at: float, deadline: float, stagger: float = 0.25, early: float = 0.5,
                 grace: float = 1.0, hedge_after: float = 1.0, max_inflight: int = 3,
                 poll_gap: float = 0.15, other_gap: float = 1.0) -> None:
        """`slots` are in order of preference. Times are time.time() values on this computer.

        early        the first choice starts this long before `open_at`, to cover clock error
        stagger      each later choice starts this much after the one before it
        grace        after the first hold, wait this long for a better choice still in flight
        hedge_after  send a second request for a slot whose reply has not come after this long
        poll_gap     pause between requests while the site says the window is not open
        """
        self.hold = hold
        self.log = log
        self.deadline = deadline
        self.grace = grace
        self.hedge_after = hedge_after
        self.max_inflight = max_inflight
        self.poll_gap = poll_gap
        self.other_gap = other_gap
        self.states = [
            SlotState(slot, rank, open_at - early if rank == 0 else open_at + stagger * rank)
            for rank, slot in enumerate(slots)
        ]
        self.cond = threading.Condition()
        self.window_closed = False     # the newest answer about the window said "not open"
        self.window_news = 0.0         # launch time of the request that answer belongs to
        self.session_ended = False
        self.stopped = False

    # ---- one request ------------------------------------------------------

    def _attempt(self, state: SlotState, launched: float) -> None:
        try:
            reply = self.hold(state.slot)
        except Exception as err:  # a worker thread must always report back
            reply = Reply(ERROR, detail=f"{type(err).__name__}: {err}")
        took = time.time() - launched
        with self.cond:
            state.inflight -= 1
            if reply.kind == HELD:
                state.urls.append(reply.url)
            elif reply.kind == TAKEN:
                state.taken_seen = True
            elif reply.kind == NOT_OPEN:
                state.gap = self.poll_gap
            elif reply.kind == SESSION:
                self.session_ended = True
            else:
                state.gap = self.other_gap
            if reply.kind in (HELD, TAKEN, NOT_OPEN) and launched > self.window_news:
                self.window_news = launched
                self.window_closed = reply.kind == NOT_OPEN
            self.log(f"{state.slot['time']} hole {state.slot['hole_no']}: {reply.kind}"
                     + (f" ({reply.detail})" if reply.detail else "") + f", reply took {took:.2f}s")
            self.cond.notify_all()

    def _launch(self, state: SlotState, now: float) -> None:
        state.inflight += 1
        state.last_launch = now
        threading.Thread(target=self._attempt, args=(state, now), daemon=True).start()

    # ---- the race ---------------------------------------------------------

    def run(self) -> Outcome:
        first_hold_at = None
        with self.cond:
            while True:
                now = time.time()
                if self.session_ended:
                    return self._finish(None, "session")
                held = [s for s in self.states if s.urls]
                if held:
                    best = min(held, key=lambda s: s.rank)
                    first_hold_at = first_hold_at or now
                    better_coming = any(s.rank < best.rank and s.inflight for s in self.states)
                    if not better_coming or now - first_hold_at >= self.grace:
                        return self._finish(best, "held")
                elif all(s.lost for s in self.states):
                    return self._finish(None, "all_taken")
                elif now >= self.deadline:
                    return self._finish(None, "timeout")
                else:
                    self._launch_due(now)
                self.cond.wait(0.01)

    def _launch_due(self, now: float) -> None:
        open_slots = [s for s in self.states if not s.taken_seen]
        for state in open_slots:
            if now < state.start:
                continue
            # While the window is closed, only the first open choice keeps asking.
            if self.window_closed and state is not open_slots[0]:
                continue
            since = now - state.last_launch
            if state.inflight == 0:
                if since >= state.gap:
                    self._launch(state, now)
            elif state.inflight < self.max_inflight:
                if since >= (self.poll_gap if self.window_closed else self.hedge_after):
                    self._launch(state, now)

    def _finish(self, best: Optional[SlotState], reason: str) -> Outcome:
        self.stopped = True
        return Outcome(best, reason, self.states)

    def drain(self, timeout: float) -> List[str]:
        """Wait for requests still in flight, then return every hold we got."""
        end = time.time() + timeout
        with self.cond:
            while any(s.inflight for s in self.states) and time.time() < end:
                self.cond.wait(0.1)
            return [url for s in self.states for url in s.urls]
