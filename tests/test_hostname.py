"""`DliPowerSwitch.assert_hostname` -- the PDU must agree with everything else about
what it is called.

A V222 ships calling itself by its hardware id: mastps00 reads 'V22251', mastps05 reads
'V22291'. A factory reset or an RMA swap puts it back there. The configuration DB, both
DNS directions and every log line this class writes already say 'mastpsNN'; the device's
own web UI, syslog and SNMP traps are the one dissenting voice, and they are what someone
reads when a unit is dark and the PDU is the suspect.

Same shape as test_recovery_mode.py, whose docstring explains why the switch is never
really constructed. Two cases here are specific to a *string* setting rather than an int:

  - the name must come from self.hostname, not conf.network.host -- NetworkConfig
    synthesises `host` by reverse DNS, and in the DB it is an FQDN, which is not what
    belongs in a hostname field;
  - the comparison must be case-insensitive, or a device that normalises what it is given
    would be rewritten on every bring-up and the comparison would never come out equal.
"""

from __future__ import annotations

import logging

import pytest

from common.config.network import NetworkConfig
from common.config.power import PowerSwitchConfig
from common.dlipowerswitch import DliPowerSwitch

FACTORY_DEFAULT = "V22251"  # what mastps00 reads today
OUR_NAME = "mastps00"


class FakeSwitch(DliPowerSwitch):
    """A DliPowerSwitch with get/put recorded rather than sent.

    Subclassed, not stubbed, so the URL and the name come from the real class. `__init__`
    deliberately does not call super() -- that is the one that talks to the device.
    """

    def __init__(self, reads, hostname: str = OUR_NAME):
        self.hostname = hostname
        self._identified = True
        self._reads = list(reads)
        self.gets: list[str] = []
        self.puts: list[tuple[str, object]] = []
        # An FQDN, as the config DB actually holds it -- the value this method must ignore.
        self.conf = PowerSwitchConfig(
            network=NetworkConfig(host=f"{hostname}.weizmann.ac.il", ipaddr="10.23.1.75"),
            userid="admin",
            password="1234",
            outlets={str(n): f"Outlet{n}" for n in range(1, 9)},
        )

    def get(self, url, params=None):
        self.gets.append(url)
        return self._reads.pop(0) if self._reads else None

    def put(self, url, data=None):
        """Returns None, as the real one does even for a write that took."""
        self.puts.append((url, data))

    def __repr__(self):
        return "[fake:10.23.1.75]"


def _assert_hostname(reads, hostname: str = OUR_NAME) -> FakeSwitch:
    """Run the real method against a fake transport primed with `reads`."""
    switch = FakeSwitch(reads, hostname)
    DliPowerSwitch.assert_hostname(switch)  # type: ignore[arg-type]
    return switch


class TestItCorrectsAWrongName:
    def test_the_factory_default_is_written(self):
        """mast00's PDU today: reads 'V22251', should read 'mastps00'."""
        switch = _assert_hostname([FACTORY_DEFAULT, OUR_NAME])

        assert switch.puts == [(DliPowerSwitch.HOSTNAME_URL, OUR_NAME)]

    def test_another_units_name_is_corrected(self):
        """A PDU moved between units keeps the name of where it used to be."""
        switch = _assert_hostname(["mastps05", OUR_NAME])

        assert switch.puts == [(DliPowerSwitch.HOSTNAME_URL, OUR_NAME)]

    def test_the_write_is_read_back(self):
        switch = _assert_hostname([FACTORY_DEFAULT, OUR_NAME])

        assert switch.gets == [DliPowerSwitch.HOSTNAME_URL] * 2, "expected read, write, read-back"

    def test_a_write_that_did_not_take_is_reported(self, caplog):
        switch = _assert_hostname([FACTORY_DEFAULT, FACTORY_DEFAULT])

        assert len(switch.puts) == 1
        assert "failed to set hostname" in caplog.text

    def test_the_correction_is_logged_with_both_names(self, caplog):
        """The line the operator greps for after a bring-up. INFO, like the recovery-mode
        correction it sits beside, so caplog has to be told to keep it."""
        caplog.set_level(logging.INFO)

        _assert_hostname([FACTORY_DEFAULT, OUR_NAME])

        assert "hostname corrected" in caplog.text
        assert FACTORY_DEFAULT in caplog.text
        assert OUR_NAME in caplog.text


class TestItLeavesACorrectSwitchAlone:
    def test_no_write_when_the_name_already_matches(self):
        """The steady state, and the path every bring-up after the first one takes."""
        switch = _assert_hostname([OUR_NAME])

        assert switch.puts == []
        assert switch.gets == [DliPowerSwitch.HOSTNAME_URL], "one read, and nothing else"

    @pytest.mark.parametrize("stored", ["MASTPS00", "MastPs00"], ids=["upper", "mixed"])
    def test_case_alone_is_not_a_difference(self, stored):
        """Hostnames are case-insensitive. Were this a plain !=, a device that normalised
        the case of what it was given would be rewritten on every bring-up for ever, and
        the comparison would never once come out equal."""
        switch = _assert_hostname([stored])

        assert switch.puts == []


class TestTheNameComesFromTheSwitchNotTheConfig:
    def test_an_fqdn_from_the_config_is_not_written(self):
        """conf.network.host is 'mastps00.weizmann.ac.il' here, as it is in the config DB.
        NetworkConfig synthesises that field by reverse DNS when only an ipaddr is given,
        so it is not a declaration -- and an FQDN is not a hostname."""
        switch = _assert_hostname([FACTORY_DEFAULT, OUR_NAME])

        (_, written) = switch.puts[0]
        assert written == OUR_NAME
        assert written != switch.conf.network.host
        assert "." not in written

    def test_it_follows_whatever_the_factory_derived(self):
        """The name is PowerSwitchFactory's 'mastNN' -> 'mastpsNN', taken as given."""
        switch = _assert_hostname([FACTORY_DEFAULT, "mastps07"], hostname="mastps07")

        assert switch.puts == [(DliPowerSwitch.HOSTNAME_URL, "mastps07")]


class TestAnUnreadableSwitchIsNotWritten:
    """Unreadable is not wrong -- the guard assert_recovery_mode needs, for a string."""

    @pytest.mark.parametrize(
        "answer",
        [
            {"error": "timeout"},
            {"error": "connection refused"},
            None,
            0,
            [],
            True,
        ],
        ids=["get() timeout", "get() http error", "common_get_put failure", "an int", "junk", "a bool"],
    )
    def test_nothing_is_written(self, answer):
        switch = _assert_hostname([answer])

        assert switch.puts == [], f"a read of {answer!r} must not trigger a write"

    def test_the_failure_is_logged_rather_than_swallowed(self, caplog):
        switch = _assert_hostname([{"error": "timeout"}])

        assert "cannot read hostname" in caplog.text
        assert switch.puts == []

    def test_an_unreadable_read_back_is_a_failure(self, caplog):
        """The write may itself have bounced the device's network stack."""
        switch = _assert_hostname([FACTORY_DEFAULT, {"error": "timeout"}])

        assert len(switch.puts) == 1
        assert "failed to set hostname" in caplog.text


class TestItRunsOnDetection:
    def test_probe_asserts_the_hostname_last(self):
        """Placement: the once-per-address config push in probe(), after the outlet names
        and the recovery mode. Last because it is the one write that can disturb the
        device's own networking, and the other two must have landed first."""
        import inspect

        source = inspect.getsource(DliPowerSwitch.probe)

        assert "assert_hostname()" in source
        assert source.index("upload_outlet_names()") < source.index("assert_recovery_mode()")
        assert source.index("assert_recovery_mode()") < source.index("assert_hostname()")

    def test_it_is_behind_identification(self):
        """It writes, so it is subject to the put() guard: an unidentified device is
        refused even if assert_hostname decides a correction is due."""
        switch = FakeSwitch([FACTORY_DEFAULT, FACTORY_DEFAULT])
        switch._identified = False
        switch.ipaddr = "10.23.1.75"
        switch.base_url = "http://10.23.1.75/"

        assert DliPowerSwitch.put(switch, DliPowerSwitch.HOSTNAME_URL, data=OUR_NAME) is None
