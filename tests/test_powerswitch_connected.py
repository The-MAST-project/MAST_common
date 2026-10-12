"""`DliPowerSwitch.connected` is `detected`: whether we can talk to the switch.

It returned a constant False. A unit's `connected` is `all()` over its components, the switch
among them, so the unit always read disconnected and every shutdown reconnected every
component -- the stage reopened an already-open device and lost it (mast00, 2026-10-12).

As in test_identify.py the switch is never really constructed (`__init__` probes the device,
starts a RepeatTimer and PUTs the outlet names), so the property is read on a bare instance.
"""

from __future__ import annotations

import httpx
import pytest

from common.dlipowerswitch import DliPowerSwitch


def _switch(detected: bool) -> DliPowerSwitch:
    switch = object.__new__(DliPowerSwitch)
    switch._detected = detected
    return switch


@pytest.mark.parametrize("detected", [True, False])
def test_connected_follows_detected(detected):
    assert _switch(detected).connected is detected


def test_a_failed_exchange_makes_it_disconnected():
    """`get` clears `detected` on a transport error, and `connected` goes with it."""

    class _Timeout:
        def get(self, **_):
            raise httpx.TimeoutException("no answer")

    switch = _switch(True)
    switch.base_url = "http://10.0.0.1/"
    switch.timeout = 1
    switch._http_client = _Timeout()

    switch.get("restapi/relay/outlets/0/state/")

    assert switch.connected is False
