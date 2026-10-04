"""`NotificationApi`'s transport: how long a send may wait."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import common.api as api
from common.api import NotificationApi

CONTROL_IPADDR = "10.23.1.181"


@pytest.fixture
def notification_api(monkeypatch):
    site = SimpleNamespace(controller_host="mast-ns-control")
    monkeypatch.setattr(api, "resolve_site", lambda site_name: site)
    monkeypatch.setattr(api, "Config", lambda: SimpleNamespace(get_service=lambda service_name: SimpleNamespace(port=8002)))
    monkeypatch.setattr(api, "load_local_config", lambda: SimpleNamespace(domain="weizmann.ac.il"))
    monkeypatch.setattr(api.socket, "gethostbyname", lambda hostname: CONTROL_IPADDR)
    monkeypatch.setattr(NotificationApi, "_instance", None)
    return NotificationApi()


def test_a_send_waits_the_notification_timeout(notification_api):
    assert notification_api.timeout == NotificationApi.NOTIFICATION_TIMEOUT
