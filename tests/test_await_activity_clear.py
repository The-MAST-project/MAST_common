"""The bounded wait on an activity flag (MAST_unit#80, API design guidelines §5.2).

The pattern this exists to replace is a bare `while self.is_active(...): time.sleep(0.2)`,
which wedges its caller for the lifetime of the process when the thing it waits for never
happens. So the two properties worth testing are the ones that pattern lacks: it comes back,
and it says which way it came back.
"""

from __future__ import annotations

import threading
import time
from enum import IntFlag, auto

from common.activities import Activities


class FakeActivities(IntFlag):
    Idle = 0
    Moving = auto()
    Aborting = auto()


class Component(Activities):
    """Bit-twiddling stands in for start_activity/end_activity on purpose.

    Those two publish a notification, which reaches `Config()` and wants a real config file --
    machinery this helper does not touch. `is_active`, which the helper does use, reads the
    bitmask under the lock, so setting it directly exercises the same state.
    """

    def __init__(self):
        Activities.__init__(self)

    def set(self, activity):
        with self.lock:
            self.activities |= activity

    def clear(self, activity):
        with self.lock:
            self.activities &= ~activity


def test_it_returns_at_once_when_the_flag_is_already_clear():
    component = Component()

    started = time.monotonic()
    assert component.await_activity_clear(FakeActivities.Aborting, timeout=5) is True
    assert time.monotonic() - started < 0.5


def test_it_returns_true_when_the_flag_clears_while_waiting():
    component = Component()
    component.set(FakeActivities.Aborting)

    def clear_it():
        time.sleep(0.3)
        component.clear(FakeActivities.Aborting)

    threading.Thread(target=clear_it, daemon=True).start()

    assert component.await_activity_clear(FakeActivities.Aborting, timeout=5, interval=0.05) is True
    assert not component.is_active(FakeActivities.Aborting)


def test_it_gives_up_at_the_deadline_instead_of_waiting_forever():
    """The defect in `Unit.abort()`: a stop that never lands used to wedge the endpoint."""
    component = Component()
    component.set(FakeActivities.Aborting)

    started = time.monotonic()
    cleared = component.await_activity_clear(FakeActivities.Aborting, timeout=0.5, interval=0.05)
    elapsed = time.monotonic() - started

    assert cleared is False
    assert 0.5 <= elapsed < 2.0, f"expected to return at the deadline, took {elapsed:.2f}s"


def test_a_timeout_leaves_the_activity_set():
    """So the component stays visibly Aborting in /status rather than claiming to be idle.

    The caller reports the failure; this helper does not tidy up after the hardware.
    """
    component = Component()
    component.set(FakeActivities.Aborting)

    component.await_activity_clear(FakeActivities.Aborting, timeout=0.3, interval=0.05)

    assert component.is_active(FakeActivities.Aborting)


def test_it_waits_only_on_the_flag_it_was_given():
    component = Component()
    component.set(FakeActivities.Moving)

    assert component.await_activity_clear(FakeActivities.Aborting, timeout=0.5) is True
    assert component.is_active(FakeActivities.Moving)
