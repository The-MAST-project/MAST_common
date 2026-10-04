"""`NotificationApi`'s transport: how long a send may wait, and whom it trusts."""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

import common.api as api
from common.api import MAST_CA_CERT, NotificationApi

CONTROL_IPADDR = "10.23.1.181"

# mast-ns-control's server certificate, as served on 2026-10-04 and kept in MAST_control at
# root/etc/ssl/certs/mast-ns-control.crt.
MAST_NS_CONTROL_CERT = """
-----BEGIN CERTIFICATE-----
MIIEbTCCAlWgAwIBAgIUcm4SOWQ4fMmNgDWZq98pYPeJLZ8wDQYJKoZIhvcNAQEL
BQAwMDEWMBQGA1UEAwwNTUFTVCBMb2NhbCBDQTEWMBQGA1UECgwNV2Vpem1hbm4g
TUFTVDAeFw0yNjA4MTEwODExMThaFw0zNjA4MDgwODExMThaMCkxJzAlBgNVBAMM
Hm1hc3QtbnMtY29udHJvbC53ZWl6bWFubi5hYy5pbDCCASIwDQYJKoZIhvcNAQEB
BQADggEPADCCAQoCggEBALQWLIFsCSXCw1RnegY8bsd40meBPuphx7E59TAUe4Jb
4nkBU14Am5E+A5aTtd2aCojuIZCjjV9GY7PlRSr7dhkKQ7k4mt+TEfWOBMXBMhoe
Gdl3Tx74ppMiCSq/C2V1rBsr/X8P2drnY5eYRifP2Gc3k8d719KhJycfhaGXizWQ
30cK0rsIN12SCdUnSUGiRlQXJb18iUkjnd4gZJ3yf9+cErqdH3H23MtVfMWslY3w
k3ItzgrdhVc9KKgIDhM2jglrSa2bBCALys4Tzz2nJi5kQVrdur8Nhb1cD/NafE5p
2KXdXCmhgXQmNkWV0RyQLlcRblOFpiP2xaJS73Xzn7sCAwEAAaOBhTCBgjBABgNV
HREEOTA3gh5tYXN0LW5zLWNvbnRyb2wud2Vpem1hbm4uYWMuaWyCD21hc3QtbnMt
Y29udHJvbIcEChcBtTAdBgNVHQ4EFgQU/J9+BkN/sW3xJ2CP2CPqawtl8pgwHwYD
VR0jBBgwFoAUYSCCKOOK90o+16qp0CFxW+T9FKcwDQYJKoZIhvcNAQELBQADggIB
AAETK/2LFm8XIw6U1vCcy/XHLZrHcs4OLsETdbU5HOjOKUpYNtZwONRnIyzy8+H3
IiXlsVy+N2qA1BjTtDib5dFGjiFSzzfWm1twtUaVfQONy6jAvX9O7+nTTvZY0xKa
UuHdkK3vCN6HhWNt9hZfVnVW1rgM055odfBJtNLWLQqH9H3ixv0HHC7qFmq1UX/F
VBZ7lNYKXgn3XlBFhh54hmP0r9RpKBiERQOhRS3HWD0P5Gjy1/Rmkz0okg887vTG
ad2FaRPUKTDrRVAQgV/MyTnt//N2+FbAHCa4K3MTYMkq773IArwWR1qMb4elKCz0
SLglKqUzTbGQzSF8BxQBtI4Iuct9jZaSl5JQSdCgbLvmiexAMMFjaHKGD1j85H61
62W4uXFL0bwCudBap/cK1jb5ugXMGpiIADCBqvFbiGK0tlI6kOn1fPJPeu26UeQ7
9wzVYb7Hv3ILJ+BXrumb4wuwriqEtVDF5qBgtbRP26KgFfAZ1U4c0ZJlGmQNrvp8
i8GJGDDzPAsA9sT+LqBoxLC4o28X4STle/tzfrgWrYTzlTYy3s016Ji09BpzH+fy
NDVhjRe08/dAMN3Ol5sBiwDwIo8PxrjOirSE9isxM3KIsE4K0DGDH6oFyiqTRwFx
2ChJ5YDxK7SETn1j9ZB1uhP2JTKoA6fRy4SplzrSeb/o
-----END CERTIFICATE-----
"""


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


@pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")
def test_the_pinned_ca_verifies_the_control_machine(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text(MAST_CA_CERT)
    leaf = tmp_path / "mast-ns-control.crt"
    leaf.write_text(MAST_NS_CONTROL_CERT)

    result = subprocess.run(["openssl", "verify", "-CAfile", str(ca), str(leaf)], capture_output=True, text=True)

    assert result.returncode == 0, result.stdout + result.stderr
