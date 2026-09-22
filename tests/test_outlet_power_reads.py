"""A power check reads the switch by name, and every power change is logged.

Two defects found together on mast03, 2026-09-22, while chasing why the covers outlet went
off at shutdown and never came back.

`MAST_unit#261` -- `SwitchedOutlet.__init__` sets `self.outlets = [self]`, and `is_on()` was
`all(outlet.state for outlet in self.outlets)`, which is `all([self.state])`. A subclass may
redefine `state`, and `Covers` does, with the mirror covers' `CoversState`. A plain `Enum`
member is truthy whatever its value -- `NotPresent` is 0 and still truthy -- so `is_on()` was
unconditionally True for the covers. Every power-on is guarded by `if not self.is_on()`, so
none ever fired, while `power_off()` in `shutdown()` is unguarded and worked. The outlet was a
one-way trip.

`MAST_common#123` -- only the ON branch logged, and only when a delay was configured, so a
power-off left no trace and three different histories produced an identical log.

The switch is never really constructed here: `__init__` probes the device, starts a
RepeatTimer and PUTs the outlet names. Outlets are built on bare instances, and the recording
switch below is the whole hardware surface these paths touch.
"""

from __future__ import annotations

import logging
from enum import Enum

import pytest

from common.dlipowerswitch import SwitchedOutlet


class RecordingSwitch:
    """The slice of `DliPowerSwitch` the power paths use, over a dict of outlet states."""

    def __init__(self, states=None, delay_after_on=0):
        self.states = dict(states or {})
        self.detected = True
        self.writes: list[tuple[str, bool]] = []

        class _Conf:
            pass

        self.conf = _Conf()
        self.conf.delay_after_on = delay_after_on

    def get_outlet_state(self, name):
        return self.states.get(name)

    def set_outlet_state(self, name, state):
        self.writes.append((name, state))
        self.states[name] = state


def _outlet(names, switch, cls=SwitchedOutlet):
    """A SwitchedOutlet wired to a recording switch, without touching a device."""
    outlet = object.__new__(cls)
    outlet.outlet_names = list(names)
    outlet.outlets = [outlet]
    outlet.group_name = None
    outlet.power_switch = switch
    outlet.delay_after_on = switch.conf.delay_after_on
    return outlet


# --------------------------------------------------------------- the shadowing defect


class ShadowState(Enum):
    """Stands in for `CoversState`: a plain Enum, so every member is truthy."""

    NotPresent = 0
    Closed = 1
    Open = 3


class OutletWithItsOwnState(SwitchedOutlet):
    """A component that redefines `state` to mean something other than power, as Covers does."""

    @property
    def state(self):
        return ShadowState.NotPresent


def test_a_plain_enum_member_is_truthy_even_at_zero():
    """The property that made the defect unconditional rather than intermittent."""
    assert bool(ShadowState.NotPresent) is True
    assert all([ShadowState.NotPresent]) is True


def test_is_on_ignores_a_subclass_that_redefines_state():
    """The regression. Before the fix this returned True for an outlet that is off, because
    it resolved `self.state` -- and no reading of the hardware could make it false."""
    switch = RecordingSwitch({"Covers": False})
    outlet = _outlet(["Covers"], switch, cls=OutletWithItsOwnState)

    assert outlet.is_on() is False
    assert outlet.is_off() is True


def test_is_on_still_reports_a_powered_outlet_that_redefines_state():
    switch = RecordingSwitch({"Covers": True})
    outlet = _outlet(["Covers"], switch, cls=OutletWithItsOwnState)

    assert outlet.is_on() is True
    assert outlet.is_off() is False


def test_the_guarded_power_on_now_fires_for_such_a_component():
    """The operator-visible end: `if not self.is_on(): self.power_on()` is how every power-on
    in the tree is written, so an always-true `is_on()` means the outlet never comes back."""
    switch = RecordingSwitch({"Covers": False})
    outlet = _outlet(["Covers"], switch, cls=OutletWithItsOwnState)

    if not outlet.is_on():
        outlet.power_on()

    assert switch.writes == [("Covers", True)]


# ----------------------------------------------------------------- plain outlets, groups


def test_a_plain_outlet_reads_its_own_state():
    switch = RecordingSwitch({"Mount": True})
    assert _outlet(["Mount"], switch).is_on() is True
    assert _outlet(["Mount"], switch).is_off() is False


def test_a_group_is_on_only_when_every_member_is():
    switch = RecordingSwitch({"A": True, "B": False})
    group = _outlet(["A", "B"], switch)

    assert group.is_on() is False
    assert group.is_off() is False  # mixed: neither fully on nor fully off


def test_an_outlet_that_resolved_to_nothing_is_not_reported_as_powered():
    """`all([])` is True, so an outlet with no names would have read as on."""
    switch = RecordingSwitch({})
    outlet = _outlet([], switch)

    assert outlet.is_on() is False
    assert outlet.is_off() is False


# ------------------------------------------------------------------------- the logging


def test_powering_off_is_logged(caplog):
    """MAST_common#123: the direction that removes a USB device left no trace at all."""
    switch = RecordingSwitch({"Covers": True})
    outlet = _outlet(["Covers"], switch)

    with caplog.at_level(logging.INFO):
        outlet.power_off()

    assert any("powered OFF" in r.message for r in caplog.records), [r.message for r in caplog.records]


def test_powering_on_is_logged_without_needing_a_delay(caplog):
    """The only evidence of a power-on used to be a message about *sleeping*, so an outlet
    with no configured delay was invisible in both directions."""
    switch = RecordingSwitch({"Mount": False}, delay_after_on=0)
    outlet = _outlet(["Mount"], switch)

    with caplog.at_level(logging.INFO):
        outlet.power_on()

    assert any("powered ON" in r.message for r in caplog.records), [r.message for r in caplog.records]


def test_nothing_is_logged_when_the_outlet_is_already_in_the_wanted_state(caplog):
    """A logged line must mean the outlet was changed, not that someone asked."""
    switch = RecordingSwitch({"Mount": True})
    outlet = _outlet(["Mount"], switch)

    with caplog.at_level(logging.INFO):
        outlet.power_on()

    assert switch.writes == []
    assert not [r for r in caplog.records if "powered" in r.message]


@pytest.mark.parametrize(("start", "call", "expected"), [(False, "power_on", True), (True, "power_off", False)])
def test_the_write_reaches_the_switch(start, call, expected):
    switch = RecordingSwitch({"Mount": start})
    outlet = _outlet(["Mount"], switch)

    getattr(outlet, call)()

    assert switch.writes == [("Mount", expected)]
