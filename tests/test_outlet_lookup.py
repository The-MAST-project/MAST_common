"""Asking a power switch for an outlet it does not have.

`list.index` raises `ValueError: 'Dome' is not in list` -- which names neither the
switch nor the outlets it does have. Nothing in the tree catches this exception, so
that bare message is what reaches the log and the operator; it is the interface, and
it is what these tests pin.

The switch is never constructed: `__init__` probes the device, starts a RepeatTimer
and PUTs the outlet names, none of which a lookup test should cause.
"""

from __future__ import annotations

import pytest

from common.dlipowerswitch import DliPowerSwitch

OUTLETS = ["Mount", "Camera", "Focuser"]


class FakeSwitch(DliPowerSwitch):
    """Real methods, no transport. Subclassed so the code under test is the real code."""

    def __init__(self):  # deliberately not super().__init__ -- that one talks to the PDU
        self.outlet_names = list(OUTLETS)
        self.hostname = "mastps00"
        self.ipaddr = "10.23.1.75"
        self._detected = True
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(f"GET {url}")
        return True

    def put(self, url, data=None):
        self.calls.append(f"PUT {url}")


def _lookup(method_name: str):
    """Call whichever method takes an outlet name, with an outlet that does not exist."""
    switch = FakeSwitch()
    args = ("Dome", True) if method_name == "set_outlet_state" else ("Dome",)
    getattr(switch, method_name)(*args)


@pytest.mark.parametrize("method", ["get_outlet_state", "set_outlet_state"])
class TestAnUnknownOutletSaysWhichAndWhere:
    def test_it_raises_value_error(self, method):
        with pytest.raises(ValueError):
            _lookup(method)

    def test_the_message_names_the_outlet_that_was_asked_for(self, method):
        with pytest.raises(ValueError, match="Dome"):
            _lookup(method)

    def test_the_message_lists_the_outlets_that_do_exist(self, method):
        """Without this the operator cannot tell a typo from a config gap."""
        with pytest.raises(ValueError) as excinfo:
            _lookup(method)

        assert all(name in str(excinfo.value) for name in OUTLETS)

    def test_the_message_names_the_switch(self, method):
        """A unit has more than one switch; "no outlet named X" alone does not say which."""
        with pytest.raises(ValueError) as excinfo:
            _lookup(method)

        assert "mastps00" in str(excinfo.value)

    def test_the_unhelpful_original_is_not_chained(self, method):
        """`from None`. Otherwise the traceback ends with "'Dome' is not in list",
        which is the message this replaces."""
        with pytest.raises(ValueError) as excinfo:
            _lookup(method)

        assert excinfo.value.__cause__ is None
        assert excinfo.value.__suppress_context__, "the original ValueError must not be printed"


class TestAKnownOutletIsUnaffected:
    """The guard must not have swallowed the normal path."""

    def test_get_reaches_the_device(self):
        switch = FakeSwitch()

        switch.get_outlet_state("Camera")

        assert switch.calls == ["GET restapi/relay/outlets/1/state/"], "index 1 is Camera"

    def test_set_reaches_the_device(self):
        switch = FakeSwitch()

        switch.set_outlet_state("Focuser", True)

        assert switch.calls == ["PUT restapi/relay/outlets/2/state/"], "index 2 is Focuser"

    def test_an_undetected_switch_still_returns_early(self):
        """The `detected` check comes first, and a bad name must not raise past it --
        callers rely on a missing switch being quiet rather than fatal."""
        switch = FakeSwitch()
        switch._detected = False

        assert switch.get_outlet_state("Dome") is None
        assert switch.calls == []
