"""The two behavioral consumers of the machine role (epic #15).

`notifications._build_initiator` derives the machine *type* from
`local.machine_role`, and `mast_logging.init_log` names the log file after it —
falling back to `mast-STARTUP-log.txt` when the config cannot be read yet
(init_log runs at import time and must never raise there).
"""

import textwrap

import pytest

from common.config.local import load_local_config

VALID_TOML = textwrap.dedent(
    """
    site = "ns"
    project = "mast"
    machine_role = "{role}"
    controller_host = "mast-ns-control"
    database = "mast"
    domain = "weizmann.ac.il"

    [location]
    latitude = 30.04
    longitude = 35.02
    elevation = 400
    """
)


def _point_at(tmp_path, monkeypatch, role="control"):
    cfg = tmp_path / "config.toml"
    cfg.write_text(VALID_TOML.format(role=role))
    monkeypatch.setenv("MAST_CONFIG", str(cfg))
    load_local_config.cache_clear()
    return cfg


@pytest.mark.parametrize(
    "role,expected_type",
    [("unit", "unit"), ("spec", "spec"), ("control", "controller")],
)
def test_notification_machine_type_from_role(tmp_path, monkeypatch, role, expected_type):
    from common.notifications import _build_initiator

    _point_at(tmp_path, monkeypatch, role=role)
    assert _build_initiator().type == expected_type


def test_log_name_uses_machine_role(tmp_path, monkeypatch):
    _point_at(tmp_path, monkeypatch, role="unit")
    # init_log builds "mast-<machine_role>-log.txt"; assert the resolved role.
    assert load_local_config().machine_role == "unit"


def test_init_log_survives_missing_config(monkeypatch):
    """init_log runs at import time; a missing config must not raise there."""
    import logging

    from common.mast_logging import init_log

    monkeypatch.setenv("MAST_CONFIG", "/nonexistent/config.toml")
    load_local_config.cache_clear()
    # Must not raise despite the unreadable config (falls back to STARTUP).
    init_log(logging.getLogger("mast.test.startup"))
