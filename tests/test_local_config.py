"""Bootstrap-config tests for the machine_role migration (epic #15).

Exercise `common.config.local`: the required, validated `machine_role` field, the
fixed config-file path (no MAST_PROJECT env var), and the fail-fast behavior of
`load_local_config()`.
"""

import textwrap

import pytest

from common.config.local import (
    VALID_MACHINE_ROLES,
    ConfigError,
    _config_file_path,
    load_local_config,
)

VALID_TOML = textwrap.dedent(
    """
    site = "ns"
    project = "mast"
    machine_role = "control"
    controller_host = "mast-ns-control"
    database = "mast"
    domain = "weizmann.ac.il"

    [location]
    latitude = 30.04
    longitude = 35.02
    elevation = 400
    """
)


def _point_at(tmp_path, toml, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text(toml)
    monkeypatch.setenv("MAST_CONFIG", str(cfg))
    load_local_config.cache_clear()
    return cfg


def test_valid_config_loads_machine_role(tmp_path, monkeypatch):
    _point_at(tmp_path, VALID_TOML, monkeypatch)
    assert load_local_config().machine_role == "control"


def test_missing_machine_role_field_fails(tmp_path, monkeypatch):
    toml = "\n".join(
        line for line in VALID_TOML.splitlines() if "machine_role" not in line
    )
    _point_at(tmp_path, toml, monkeypatch)
    with pytest.raises(ConfigError) as excinfo:
        load_local_config()
    assert "machine_role" in str(excinfo.value)


def test_invalid_machine_role_value_fails(tmp_path, monkeypatch):
    toml = VALID_TOML.replace('machine_role = "control"', 'machine_role = "gui"')
    _point_at(tmp_path, toml, monkeypatch)
    with pytest.raises(ConfigError) as excinfo:
        load_local_config()
    assert "expected one of" in str(excinfo.value)


@pytest.mark.parametrize("role", VALID_MACHINE_ROLES)
def test_every_valid_role_accepted(tmp_path, monkeypatch, role):
    toml = VALID_TOML.replace('machine_role = "control"', f'machine_role = "{role}"')
    _point_at(tmp_path, toml, monkeypatch)
    assert load_local_config().machine_role == role


def test_fixed_default_path_used_and_env_inert(monkeypatch):
    """No MAST_CONFIG -> fixed config.toml path; MAST_PROJECT must not matter."""
    monkeypatch.delenv("MAST_CONFIG", raising=False)
    monkeypatch.setenv("MAST_PROJECT", "unit")  # legacy var: now inert
    assert _config_file_path().endswith("config.toml")
    assert "unit" not in _config_file_path()


def test_missing_file_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("MAST_CONFIG", str(tmp_path / "nope.toml"))
    load_local_config.cache_clear()
    with pytest.raises(ConfigError) as excinfo:
        load_local_config()
    assert "does not exist" in str(excinfo.value)


def test_malformed_toml_fails(tmp_path, monkeypatch):
    _point_at(tmp_path, "site = = broken", monkeypatch)
    with pytest.raises(ConfigError):
        load_local_config()
