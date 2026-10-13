"""The test suites stay off the internet unless a test is marked ``network`` (common/testing/network.py)."""

from __future__ import annotations

import socket

import pytest

from common.testing import network

# TEST-NET-1 (RFC 5737): never routed, so nothing is reached even if the guard failed open.
OFF_MACHINE = ("192.0.2.1", 9)


def test_a_connection_off_this_machine_fails_the_test():
    with pytest.raises(network.NetworkAccessError, match="192.0.2.1"):
        socket.create_connection(OFF_MACHINE, timeout=1)


def test_so_does_a_bare_socket_connect():
    with socket.socket() as s, pytest.raises(network.NetworkAccessError):
        s.connect(OFF_MACHINE)
    with socket.socket() as s, pytest.raises(network.NetworkAccessError):
        s.connect_ex(OFF_MACHINE)


def test_loopback_is_allowed():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass


def test_allowed_lifts_the_guard_and_restores_it():
    with network.allowed():
        network._refuse("test", OFF_MACHINE)  # would raise if the guard were up
    with pytest.raises(network.NetworkAccessError):
        network._refuse("test", OFF_MACHINE)


@pytest.mark.parametrize("variable", network.CI_VARIABLES)
def test_in_ci_network_tests_are_skipped_without_probing(variable):
    probed = []

    reason = network.skip_reason({variable: "true"}, lambda: probed.append(1))

    assert reason and variable in reason
    assert probed == [], "no reaching out from a runner, not even to probe"


def test_elsewhere_they_run_when_the_probe_succeeds():
    assert network.skip_reason({}, lambda: None) is None


def test_and_are_skipped_with_the_probe_reason_when_it_fails():
    reason = network.skip_reason({}, lambda: "https://iers.example unreachable: timed out")

    assert reason and "timed out" in reason


def test_the_marker_is_registered(pytestconfig):
    assert any(line.startswith("network:") for line in pytestconfig.getini("markers"))


@pytest.mark.network
def test_the_real_iers_source_is_fetched_through_the_site_proxy(tmp_path):
    """End to end: download, validate and store a table, the way the unit's refresher does.

    This is the path that broke on mast00 on 2026-10-12, when the fetch went direct instead of
    through the proxy. About 3.8 MB; never run in CI.
    """
    from common import iers_policy

    stored = iers_policy.fetch_once(str(tmp_path))

    assert stored is not None, "no table was stored -- see the log for why the fetch failed"
