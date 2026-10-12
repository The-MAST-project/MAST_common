"""`common.internet`: the proxy for reaching the internet, not the library's own lookup.

mast00, 2026-10-12: inside the unit, whose app.py deletes http(s)_proxy but leaves no_proxy,
urllib's getproxies() returned only {'no': ...} -- the registry is read only when the
environment holds no proxy setting at all -- so the IERS fetch and object-name resolution both
went direct and timed out.
"""

from __future__ import annotations

import pytest

from common import internet

PROXY = "http://bcproxy.example:8080"


@pytest.fixture
def env(monkeypatch):
    for name in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    def set_env(**values):
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return set_env


def test_the_environment_wins_when_it_names_a_proxy(env, monkeypatch):
    env(https_proxy=PROXY, http_proxy=PROXY)
    monkeypatch.setattr(internet.urllib.request, "getproxies_registry", lambda: {"https": "http://other:1"})

    assert internet.internet_proxies() == {"http": PROXY, "https": PROXY}


def test_no_proxy_alone_does_not_hide_the_registry(env, monkeypatch):
    """The unit's environment exactly: no_proxy set, http(s)_proxy deleted."""
    env(no_proxy="localhost,127.0.0.1,10.23.0.0/16")
    monkeypatch.setattr(internet.sys, "platform", "win32")
    monkeypatch.setattr(internet.urllib.request, "getproxies_registry", lambda: {"http": PROXY, "https": PROXY})

    assert internet.internet_proxies() == {"http": PROXY, "https": PROXY}


def test_off_windows_with_nothing_set_it_goes_direct(env, monkeypatch):
    env()
    monkeypatch.setattr(internet.sys, "platform", "linux")

    assert internet.internet_proxies() == {}


@pytest.mark.parametrize(
    ("url", "expected"),
    [("https://cds.unistra.fr/x", "https-proxy"), ("http://vizier.cfa.harvard.edu/x", "http-proxy"), ("ftp://x/y", None)],
)
def test_internet_proxy_picks_by_scheme(monkeypatch, url, expected):
    monkeypatch.setattr(internet, "internet_proxies", lambda: {"http": "http-proxy", "https": "https-proxy"})

    assert internet.internet_proxy(url) == expected


def test_tls_context_verifies():
    context = internet.tls_context()

    assert context.check_hostname is True
