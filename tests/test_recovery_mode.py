"""`DliPowerSwitch.assert_recovery_mode` -- the PDU must come back from a power cut.

The V222 ships at recovery_mode 0 (all outlets off when mains returns), which strands
the UNIT-PC: the machine one would use to fix it is the one that is off. MAST_unit#50.

The switch is never constructed here. `__init__` probes the device, starts a RepeatTimer
and PUTs the outlet names, so building one to test a decision would put real traffic on
the power VLAN; the methods are called on a bare instance instead.

The case that matters most is the one that is hardest to see: an unreadable switch must
not be written to. A failed read has two shapes -- `{'error': ...}` from get() on a
timeout, and None from common_get_put on an HTTP or JSON error -- and neither is a
recovery mode. Treating either as "not 2" would fire a PUT at a device we cannot read.
"""

from __future__ import annotations

import pytest

from common.dlipowerswitch import DliPowerSwitch


class FakeSwitch(DliPowerSwitch):
    """A DliPowerSwitch with get/put recorded rather than sent.

    Subclassed, not stubbed, so the URL and the desired value come from the real class:
    a fake carrying its own copies would keep passing if either changed. `__init__`
    deliberately does not call super() -- that is the one that talks to the device.
    """

    def __init__(self, reads):
        self._reads = list(reads)
        self.gets: list[str] = []
        self.puts: list[tuple[str, object]] = []

    def get(self, url, params=None):
        self.gets.append(url)
        return self._reads.pop(0) if self._reads else None

    def put(self, url, data=None):
        """Returns None, as the real one does even for a write that took."""
        self.puts.append((url, data))

    def __repr__(self):
        return "[fake:10.0.0.1]"


def _assert_recovery_mode(reads) -> FakeSwitch:
    """Run the real method against a fake transport primed with `reads`."""
    switch = FakeSwitch(reads)
    DliPowerSwitch.assert_recovery_mode(switch)  # type: ignore[arg-type]
    return switch


class TestItCorrectsAWrongValue:
    @pytest.mark.parametrize("wrong", [0, 1], ids=["0 all-off (factory default)", "1 all-on"])
    def test_a_wrong_mode_is_written(self, wrong):
        switch = _assert_recovery_mode([wrong, DliPowerSwitch.DESIRED_RECOVERY_MODE])

        assert switch.puts == [(DliPowerSwitch.RECOVERY_MODE_URL, {"value": 2})]

    def test_the_write_is_read_back(self):
        """put() returns None even when it worked, so its return value cannot confirm
        anything. Without a read-back the log would claim corrections that never took."""
        switch = _assert_recovery_mode([0, DliPowerSwitch.DESIRED_RECOVERY_MODE])

        assert switch.gets == [DliPowerSwitch.RECOVERY_MODE_URL] * 2, "expected read, write, read-back"

    def test_a_write_that_did_not_take_is_reported(self, caplog):
        """The device still reads 0 after the PUT."""
        switch = _assert_recovery_mode([0, 0])

        assert len(switch.puts) == 1
        assert "failed to set recovery_mode" in caplog.text


class TestItLeavesACorrectSwitchAlone:
    def test_no_write_when_already_at_the_desired_value(self):
        """The acceptance criterion: no redundant PUT. Every unit PDU measured so far
        already reads 2, so this is the path that runs on every bring-up in the fleet."""
        switch = _assert_recovery_mode([DliPowerSwitch.DESIRED_RECOVERY_MODE])

        assert switch.puts == []
        assert switch.gets == [DliPowerSwitch.RECOVERY_MODE_URL], "one read, and nothing else"


class TestAnUnreadableSwitchIsNotWritten:
    """Unreadable is not wrong. This is the guard the two failure shapes demand."""

    @pytest.mark.parametrize(
        "answer",
        [
            {"error": "timeout"},
            {"error": "connection refused"},
            None,
            "2",
            [],
        ],
        ids=["get() timeout", "get() http error", "common_get_put failure", "a string", "junk"],
    )
    def test_nothing_is_written(self, answer):
        switch = _assert_recovery_mode([answer])

        assert switch.puts == [], f"a read of {answer!r} must not trigger a write"

    def test_the_failure_is_logged_rather_than_swallowed(self, caplog):
        switch = _assert_recovery_mode([{"error": "timeout"}])

        assert "cannot read recovery_mode" in caplog.text
        assert switch.puts == []

    def test_a_bool_is_not_a_recovery_mode(self):
        """`isinstance(True, int)` is True in Python, so a bare int check would accept
        True as mode 1 and PUT over it. The device returns an int; a bool is a wrong
        answer from something that is not the device."""
        switch = _assert_recovery_mode([True])

        assert switch.puts == []


class TestItRunsOnDetection:
    def test_probe_asserts_the_mode_after_uploading_outlet_names(self):
        """Placement: the once-per-address config push in probe(), where the outlet
        names already go -- not the constructor, which is what MAST_unit#91 warns about."""
        import inspect

        source = inspect.getsource(DliPowerSwitch.probe)

        assert "upload_outlet_names()" in source
        assert "assert_recovery_mode()" in source
        assert source.index("upload_outlet_names()") < source.index("assert_recovery_mode()"), (
            "the names push must come first, as it did before"
        )
