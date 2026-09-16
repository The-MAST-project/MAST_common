"""`DliPowerSwitch.identify` -- prove the far end is our PDU before touching it.

A configured ipaddr is not a promise. One was taken over by a PLC, and the switch talked
to it: probe() read detection off the transport alone --

    result = self.get("restapi/relay/outlets/0/state/")
    self._detected = not (isinstance(result, dict) and "error" in result)

-- and common_get_put() answers None both for an HTTP error and for a body that is not
JSON. None is not a dict carrying 'error', so a device that merely answered HTTP was
marked detected, and upload_outlet_names() and assert_recovery_mode() then wrote into it.
Both squatter shapes passed: HTML with a 200, and the 400 that the something at
10.23.2.102 gives (MAST_unit#50).

So the tests below are mostly about rejection, and the ordering of the rejections. The
challenge check runs first because it is the only one that completes before 'admin:1234'
is offered to an unknown device; `TestTheChallengeComesBeforeTheCredentials` is what
pins that down, and it is the test to keep if any are ever dropped.

As in test_recovery_mode.py the switch is never really constructed -- `__init__` probes
the device, starts a RepeatTimer and PUTs the outlet names -- so the methods are called
on a bare instance. The one real object is the PowerSwitchConfig: it is what would notice
if the `serial` field were dropped from the model. Its NetworkConfig is given both `host`
and `ipaddr` so that its validator resolves neither, and the suite does no DNS.
"""

from __future__ import annotations

import logging
from typing import ClassVar

import httpx
import pytest

from common.config.network import NetworkConfig
from common.config.power import PowerSwitchConfig
from common.dlipowerswitch import DliPowerSwitch

# As sent by a V222 on firmware 1.13.11.0, verbatim. The realm is 'DLI <serial>', which is
# why the challenge alone carries enough to recognise the vendor.
DIGEST_CHALLENGE = (
    'Digest algorithm="MD5", realm="DLI V2222805005191", qop="auth,auth-int", '
    'nonce="xmQQP1EN68j3p50Y", opaque="8LxMz5mEsqnUCCeB"'
)
SERIAL = "V2222805005191"

_REAL_HTTPX_CLIENT = httpx.Client  # captured before any test patches the name


def answering(status: int = 401, headers: dict | None = None, body: bytes = b"{}"):
    """A MockTransport handler standing in for whatever holds the address."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers or {}, content=body)

    return handler


A_REAL_DLI = answering(401, {"WWW-Authenticate": DIGEST_CHALLENGE})


@pytest.fixture
def challenge(monkeypatch):
    """Put a fake device on the other end of identify()'s unauthenticated request.

    identify() builds its own `httpx.Client` -- it cannot reuse self._http_client, which
    carries the digest credentials -- so there is no transport to inject and the class is
    patched instead. Everything else stays real httpx, so status codes and header parsing
    behave as they do against the PDU.
    """

    def install(handler):
        def factory(*args, **kwargs):
            kwargs.pop("trust_env", None)
            return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(httpx, "Client", factory)

    return install


def a_config(serial: str | None = None) -> PowerSwitchConfig:
    return PowerSwitchConfig(
        network=NetworkConfig(host="mastps99", ipaddr="10.0.0.1"),
        userid="admin",
        password="1234",
        serial=serial,
        outlets={str(n): f"Outlet{n}" for n in range(1, 9)},
    )


class FakeSwitch(DliPowerSwitch):
    """A DliPowerSwitch with the authenticated reads served from a dict.

    Subclassed, not stubbed, so the URLs and the expected vendor come from the real class:
    a fake carrying its own copies would keep passing if either changed. `__init__`
    deliberately does not call super() -- that is the one that talks to the device.
    """

    # Shadows the real registry, so a probe() test never writes into the class attribute
    # that production instances share.
    _instantiated: ClassVar[list[str]] = []

    def __init__(self, reads: dict | None = None, serial: str | None = None):
        self.ipaddr = "10.0.0.1"
        self.base_url = "http://10.0.0.1/"
        self.timeout = 1
        self.conf = a_config(serial)
        self._detected = False
        self._identified = False
        self._identity_complaint = None
        self.gets: list[str] = []
        self.pushed: list[str] = []
        self.puts: list[tuple[str, object]] = []
        FakeSwitch._instantiated.clear()

        self._reads: dict = {
            "restapi/relay/outlets/0/state/": False,
            DliPowerSwitch.VENDOR_URL: DliPowerSwitch.VENDOR,
            DliPowerSwitch.OUTLETS_URL: [{}] * DliPowerSwitch.NUM_OUTLETS,
            DliPowerSwitch.SERIAL_URL: SERIAL,
        }
        self._reads.update(reads or {})

    def get(self, url, params=None):
        self.gets.append(url)
        return self._reads[url]

    def put(self, url, data=None):
        self.puts.append((url, data))

    def upload_outlet_names(self):
        self.pushed.append("names")

    def assert_recovery_mode(self):
        self.pushed.append("recovery")

    def assert_hostname(self):
        self.pushed.append("hostname")

    def __repr__(self):
        return "[fake:10.0.0.1]"


class TestItAcceptsARealDli:
    def test_a_v222_is_identified(self, challenge):
        challenge(A_REAL_DLI)

        assert FakeSwitch().identify() is True

    def test_all_three_checks_are_read(self, challenge):
        """Vendor, outlet map, serial. The serial is read even when unpinned, so that the
        value can be logged and copied into the configuration."""
        challenge(A_REAL_DLI)
        switch = FakeSwitch()

        switch.identify()

        assert switch.gets == [
            DliPowerSwitch.VENDOR_URL,
            DliPowerSwitch.OUTLETS_URL,
            DliPowerSwitch.SERIAL_URL,
        ]

    def test_an_unpinned_serial_is_logged_for_the_operator(self, challenge, caplog):
        challenge(A_REAL_DLI)
        caplog.set_level(logging.INFO)

        FakeSwitch().identify()

        assert SERIAL in caplog.text
        assert "not pinned" in caplog.text


class TestTheChallengeComesBeforeTheCredentials:
    """The check that matters architecturally: a device that fails the challenge is never
    offered 'admin:1234'. `switch.gets == []` is the assertion doing that work -- every
    authenticated read goes through get(), so an empty list means none was attempted."""

    @pytest.mark.parametrize(
        "handler",
        [
            answering(200, body=b"<html><body>PLC</body></html>"),
            answering(400),
            answering(404),
            answering(401, {"WWW-Authenticate": 'Basic realm="DLI V2222805005191"'}),
            answering(401, {"WWW-Authenticate": 'Digest realm="Router"'}),
            answering(401),
        ],
        ids=[
            "HTML with a 200 (a PLC)",
            "400 (10.23.2.102)",
            "404 (the safety controller)",
            "Basic auth, DLI-shaped realm",
            "Digest, foreign realm",
            "401 with no challenge header",
        ],
    )
    def test_a_squatter_is_rejected(self, challenge, handler):
        challenge(handler)
        switch = FakeSwitch()

        assert switch.identify() is False
        assert switch.gets == [], "credentials were offered to an unidentified device"

    def test_an_unreachable_address_is_rejected(self, challenge):
        def refuse(request):
            raise httpx.ConnectError("connection refused")

        challenge(refuse)
        switch = FakeSwitch()

        assert switch.identify() is False
        assert switch.gets == []

    def test_the_rejection_names_the_address(self, challenge, caplog):
        challenge(answering(400))

        FakeSwitch().identify()

        assert "NOT a DLI power switch at 10.0.0.1" in caplog.text


class TestTheAuthenticatedChecks:
    @pytest.mark.parametrize(
        "vendor",
        [None, "Acme Automation", "digital loggers, inc.", ""],
        ids=["None (a non-JSON body)", "another vendor", "wrong case", "empty"],
    )
    def test_a_foreign_vendor_is_rejected(self, challenge, vendor):
        challenge(A_REAL_DLI)

        assert FakeSwitch({DliPowerSwitch.VENDOR_URL: vendor}).identify() is False

    @pytest.mark.parametrize(
        "outlets",
        [[{}] * 4, [{}] * 16, [], None, "eight"],
        ids=["too few", "too many", "none at all", "not a list", "a string"],
    )
    def test_a_different_outlet_map_is_rejected(self, challenge, outlets):
        """upload_outlet_names() is about to write names into outlets 0..7. A device whose
        outlet map is a different shape must not receive them."""
        challenge(A_REAL_DLI)

        assert FakeSwitch({DliPowerSwitch.OUTLETS_URL: outlets}).identify() is False


class TestTheSerialPin:
    """The only check that separates our PDU from a different DLI -- which in a fleet of
    twenty is the likelier collision: two units' configurations on one address."""

    def test_a_matching_serial_passes(self, challenge):
        challenge(A_REAL_DLI)

        assert FakeSwitch(serial=SERIAL).identify() is True

    def test_another_dli_is_rejected(self, challenge):
        challenge(A_REAL_DLI)
        switch = FakeSwitch({DliPowerSwitch.SERIAL_URL: "V2229900000000"}, serial=SERIAL)

        assert switch.identify() is False

    def test_the_pin_is_opt_in(self, challenge):
        """Unset, identification still succeeds: pinning would fail an RMA swap until the
        DB is updated, and that must be a deliberate choice."""
        challenge(A_REAL_DLI)

        assert FakeSwitch(serial=None).identify() is True

    def test_the_model_carries_the_field(self):
        assert a_config(SERIAL).serial == SERIAL
        assert a_config().serial is None


class TestRejectionsAreLoggedOnce:
    """probe() runs every 5 seconds. A squatter left on the address overnight would
    otherwise write the same line some 17000 times into the night's log."""

    def test_the_same_reason_is_not_repeated(self, challenge, caplog):
        challenge(answering(400))
        switch = FakeSwitch()

        switch.identify()
        switch.identify()
        switch.identify()

        assert caplog.text.count("NOT a DLI power switch") == 1

    def test_a_new_reason_is_logged(self, challenge, caplog):
        switch = FakeSwitch()
        challenge(answering(400))
        switch.identify()

        challenge(answering(404))
        switch.identify()

        assert caplog.text.count("NOT a DLI power switch") == 2

    def test_success_clears_the_complaint(self, challenge):
        """Otherwise a device that failed, was fixed, and later failed the same way again
        would be silent the second time."""
        switch = FakeSwitch()
        challenge(answering(400))
        switch.identify()

        challenge(A_REAL_DLI)
        switch.identify()

        assert switch._identity_complaint is None


class TestProbeRequiresIdentification:
    """The wiring. Everything downstream is behind self.detected, so this is the single
    join that keeps reads and writes off an unidentified device."""

    def test_a_squatter_is_not_detected(self, challenge):
        challenge(answering(200, body=b"<html><body>PLC</body></html>"))
        switch = FakeSwitch()

        switch.probe()

        assert switch.detected is False
        assert switch._identified is False

    def test_nothing_is_pushed_to_a_squatter(self, challenge):
        """The incident itself: outlet names and the recovery mode went into the PLC."""
        challenge(answering(200, body=b"<html><body>PLC</body></html>"))
        switch = FakeSwitch()

        switch.probe()

        assert switch.pushed == []

    def test_a_real_dli_is_detected_and_configured(self, challenge):
        challenge(A_REAL_DLI)
        switch = FakeSwitch()

        switch.probe()

        assert switch.detected is True
        assert switch._identified is True
        assert switch.pushed == ["names", "recovery", "hostname"], "the names push must come first, as it did before"

    def test_an_unreachable_switch_is_not_probed_for_identity(self, challenge):
        """A timeout says nothing about what is at the address, and identify() would open
        a second connection to find out. reachable is checked first for that reason."""
        challenge(A_REAL_DLI)
        switch = FakeSwitch({"restapi/relay/outlets/0/state/": {"error": "timeout"}})

        switch.probe()

        assert switch.detected is False
        assert switch.gets == ["restapi/relay/outlets/0/state/"], "identify() should not have run"


class TestPutRefusesWithoutIdentification:
    """Defence in depth: every caller reaches put() via self.detected, but a
    DliPowerSwitch built directly, bypassing PowerSwitchFactory, never ran probe()."""

    class Exploding:
        def __getattr__(self, name):
            raise AssertionError(f"put() reached the network: _http_client.{name}")

    def _unidentified(self) -> FakeSwitch:
        switch = FakeSwitch()
        switch._http_client = self.Exploding()
        switch.headers = {}
        return switch

    def test_no_write_is_attempted(self):
        switch = self._unidentified()

        assert DliPowerSwitch.put(switch, DliPowerSwitch.RECOVERY_MODE_URL, data={"value": 2}) is None

    def test_the_refusal_is_logged(self, caplog):
        switch = self._unidentified()

        DliPowerSwitch.put(switch, "restapi/relay/outlets/0/name/", data="Mount")

        assert "refusing to write" in caplog.text

    def test_an_identified_switch_is_not_blocked(self):
        """The guard must not be the reason a real write never happens."""
        switch = self._unidentified()
        switch._identified = True

        with pytest.raises(AssertionError, match="reached the network"):
            DliPowerSwitch.put(switch, DliPowerSwitch.RECOVERY_MODE_URL, data={"value": 2})
