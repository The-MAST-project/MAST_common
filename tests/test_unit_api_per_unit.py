"""`UnitApi` is one instance per unit, not one per process."""

from __future__ import annotations

import socket

import pytest

import common.api as api
from common.api import UnitApi

ADDRESSES = {"mast01": "10.23.1.101", "mast02": "10.23.1.102"}


@pytest.fixture(autouse=True)
def fresh_instances(monkeypatch):
    """No DNS, and no instances left over from another test."""
    monkeypatch.setattr(api.socket, "gethostname", lambda: "mast-ns-control")
    monkeypatch.setattr(api.socket, "gethostbyname", lambda name: ADDRESSES[name])

    def no_reverse_dns(ipaddr):
        raise socket.herror

    monkeypatch.setattr(api.socket, "gethostbyaddr", no_reverse_dns)
    monkeypatch.setattr(UnitApi, "_instances", {})


def test_same_name_is_the_same_instance():
    assert UnitApi(hostname="mast01") is UnitApi(hostname="mast01")


def test_each_unit_gets_its_own_instance():
    first = UnitApi(hostname="mast01")
    second = UnitApi(hostname="mast02")

    assert first is not second
    assert second.base_url == "http://10.23.1.102:8000/mast/api/v1/unit"
    assert first.base_url == "http://10.23.1.101:8000/mast/api/v1/unit"


def test_ipaddr_is_a_key_too():
    by_address = UnitApi(ipaddr="10.23.1.102")

    assert by_address is UnitApi(ipaddr="10.23.1.102")
    assert by_address is not UnitApi(ipaddr="10.23.1.101")
    assert by_address.base_url == "http://10.23.1.102:8000/mast/api/v1/unit"


def test_neither_name_nor_address_is_refused():
    with pytest.raises(ValueError, match="hostname"):
        UnitApi()
