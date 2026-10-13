"""Keep the test suites off the internet, except for tests marked ``network``.

A test that reaches the internet -- the IERS data centre, Sesame, TNS -- behaves differently on
a GitHub runner, on a unit behind the Weizmann proxy, and on a machine with no route out. So:

* **Every connection that is not loopback fails the test**, with `NetworkAccessError` naming
  the address. Installed at conftest import (`install`), so it covers collection as well as the
  tests, like the process guard beside it.
* **A test marked ``@pytest.mark.network`` may connect.** It is skipped in CI (`CI` or
  `GITHUB_ACTIONS` set), so nothing reaches IERS from GitHub; elsewhere it is skipped when one
  probe of the IERS host -- through `common.internet`'s proxy, exactly as the fetch goes --
  fails, so a unit behind the site proxy counts as reachable. ``-m "not network"`` deselects
  them outright.

Wired up from a ``tests/conftest.py``::

    from common.testing import network as _network

    _network.install()
    pytest_configure = _network.pytest_configure
    pytest_collection_modifyitems = _network.pytest_collection_modifyitems
    _allow_network_for_marked_tests = _network.allow_network_for_marked_tests

A tripwire, not a sandbox: it patches `socket.socket.connect` / `connect_ex` and
`socket.create_connection`, which is what urllib, http.client, httpx's sync transport and
pymongo go through. Overlapped (asyncio proactor) connects on Windows go around it.
"""

from __future__ import annotations

import os
import socket
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

import pytest

MARKER = "network"
MARKER_HELP = (
    "network: reaches the internet (the real IERS source, via the site proxy); "
    "skipped in CI and when the IERS host is unreachable"
)
CI_VARIABLES = ("CI", "GITHUB_ACTIONS")
PROBE_TIMEOUT_SECONDS = 10

_allowed = False
_installed = False


class NetworkAccessError(OSError):
    """Raised when a test not marked ``network`` opens a connection off this machine."""


def _is_local(address) -> bool:
    if not isinstance(address, tuple):  # AF_UNIX path, or the like
        return True
    host = str(address[0]).lower()
    return host in ("localhost", "::1", "0:0:0:0:0:0:0:1") or host.startswith("127.")


def _refuse(how: str, address) -> None:
    if _allowed or _is_local(address):
        return
    raise NetworkAccessError(
        f"the test suite tried to reach {address!r} via {how}. Tests stay off the network; "
        f"mark a test that genuinely needs it @pytest.mark.{MARKER} "
        "(it is then skipped in CI and when the IERS host is unreachable)."
    )


def install() -> None:
    """Patch the socket entry points. Idempotent; call at conftest import time."""
    global _installed
    if _installed:
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def connect(self, address):
        _refuse("socket.connect", address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _refuse("socket.connect_ex", address)
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):
        _refuse("socket.create_connection", address)
        return real_create_connection(address, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection
    _installed = True


@contextmanager
def allowed() -> Iterator[None]:
    """Lift the guard for the duration."""
    global _allowed
    previous, _allowed = _allowed, True
    try:
        yield
    finally:
        _allowed = previous


def probe_iers() -> str | None:
    """None if the IERS source answers (through the site proxy, as the fetch goes); else why not."""
    from astropy.utils import iers

    from common.internet import internet_proxies, tls_context

    url = iers.conf.iers_auto_url
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(internet_proxies()),
        urllib.request.HTTPSHandler(context=tls_context()),
    )
    request = urllib.request.Request(url, method="HEAD")
    try:
        with allowed(), opener.open(request, timeout=PROBE_TIMEOUT_SECONDS):
            return None
    except Exception as ex:  # noqa: BLE001 -- DNS, proxy, TLS, timeout, HTTP error: all "unreachable"
        return f"{url} unreachable: {ex}"


def skip_reason(environ: Mapping[str, str], probe: Callable[[], str | None]) -> str | None:
    """Why network tests are skipped here, or None to run them. `probe` is called only if needed."""
    for name in CI_VARIABLES:
        if environ.get(name):
            return f"network tests do not run in CI ({name} is set)"
    reason = probe()
    return f"network tests skipped: {reason}" if reason else None


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", MARKER_HELP)


def pytest_collection_modifyitems(config, items) -> None:
    marked = [item for item in items if item.get_closest_marker(MARKER)]
    if not marked:
        return  # no probe unless a network test was actually selected
    reason = skip_reason(os.environ, probe_iers)
    if reason:
        for item in marked:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def allow_network_for_marked_tests(request):
    """Lift the guard for a test marked ``network`` (one that was not skipped)."""
    if request.node.get_closest_marker(MARKER) is None:
        yield
        return
    with allowed():
        yield
