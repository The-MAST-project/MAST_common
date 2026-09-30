"""How `BaseApi` turns a hostname into an address (#133).

A bare name is qualified with the local domain before it is tried bare. On the units a bare
lookup never goes through DNS (MAST_provisioning#228): it falls to an LLMNR/NetBIOS
broadcast that only the named machine answers, so it fails exactly when that machine is
down, while its fully-qualified name still resolves.
"""

from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

import common.api as api
from common.api import ApiDomain, BaseApi

DOMAIN = "example.org"
FQDN_ADDR = "10.0.0.1"
BARE_ADDR = "10.0.0.2"


@pytest.fixture
def lookups(monkeypatch):
    """Records every name looked up; a name resolves only if the test put it in `known`."""
    known: dict[str, str] = {}
    asked: list[str] = []

    def fake_gethostbyname(name: str) -> str:
        asked.append(name)
        if name in known:
            return known[name]
        raise socket.gaierror(11001, "getaddrinfo failed")

    monkeypatch.setattr(socket, "gethostbyname", fake_gethostbyname)
    monkeypatch.setattr(socket, "gethostname", lambda: "this-machine")
    monkeypatch.setattr(api, "load_local_config", lambda: SimpleNamespace(domain=DOMAIN))
    return SimpleNamespace(known=known, asked=asked)


def make(hostname: str) -> BaseApi:
    return BaseApi(hostname=hostname, port=8000, domain=ApiDomain.Control)


def test_a_bare_name_resolves_through_its_fully_qualified_form(lookups):
    lookups.known[f"mast-ns-control.{DOMAIN}"] = FQDN_ADDR

    assert make("mast-ns-control").ipaddr == FQDN_ADDR


def test_the_fully_qualified_form_is_tried_first(lookups):
    lookups.known[f"mast-ns-control.{DOMAIN}"] = FQDN_ADDR
    lookups.known["mast-ns-control"] = BARE_ADDR

    assert make("mast-ns-control").ipaddr == FQDN_ADDR
    assert lookups.asked == [f"mast-ns-control.{DOMAIN}"]


def test_the_bare_name_is_the_fallback(lookups):
    lookups.known["mast-ns-control"] = BARE_ADDR

    assert make("mast-ns-control").ipaddr == BARE_ADDR
    assert lookups.asked == [f"mast-ns-control.{DOMAIN}", "mast-ns-control"]


def test_a_dotted_name_is_looked_up_as_given(lookups):
    lookups.known["mast-ns-control.elsewhere.net"] = FQDN_ADDR

    assert make("mast-ns-control.elsewhere.net").ipaddr == FQDN_ADDR
    assert lookups.asked == ["mast-ns-control.elsewhere.net"]


def test_an_unresolvable_name_still_raises(lookups):
    with pytest.raises(socket.gaierror):
        make("mast-ns-control")


def test_the_address_goes_into_the_base_url(lookups):
    lookups.known[f"mast-ns-control.{DOMAIN}"] = FQDN_ADDR

    assert make("mast-ns-control").base_url.startswith(f"http://{FQDN_ADDR}:8000")
