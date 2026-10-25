"""Bootstrap-config tests for the machine_role migration (epic #15).

Exercise `common.config.local`: the required, validated `machine_role` field, the
fixed config-file path (no MAST_PROJECT env var), and the fail-fast behavior of
`load_local_config()`.
"""

import textwrap

import pytest
from pydantic import ConfigDict

import common.config.local as local_module
from common.config.local import (
    VALID_MACHINE_ROLES,
    ConfigError,
    LocalConfig,
    _config_file_path,
    load_local_config,
    share_path,
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
    toml = "\n".join(line for line in VALID_TOML.splitlines() if "machine_role" not in line)
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


# --- Z: is the share, spelled by UNC ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Z:", "//mast-ns-control/mast-share/"),
        ("Z:\\", "//mast-ns-control/mast-share/"),
        ("Z:/MAST/", "//mast-ns-control/mast-share/MAST/"),
        (r"Z:\MAST\mast00\x.fits", "//mast-ns-control/mast-share/MAST/mast00/x.fits"),
        (r"z:\\MAST//mast00\\", "//mast-ns-control/mast-share/MAST/mast00/"),
        # Not the share: unchanged.
        ("Z:foo", "Z:foo"),
        (r"C:\MAST\x", r"C:\MAST\x"),
        ("//mast-ns-control/mast-share/MAST/", "//mast-ns-control/mast-share/MAST/"),
        ("Zeta: a name", "Zeta: a name"),
        ("", ""),
    ],
)
def test_share_path(value, expected):
    assert share_path(value, "mast-ns-control") == expected


def test_loading_rewrites_every_z_path_in_the_file(tmp_path, monkeypatch):
    # No LocalConfig field holds a path yet, and extra keys are dropped -- so load into a
    # model that keeps them, to see what the loader handed it.
    class Keeps(LocalConfig):
        model_config = ConfigDict(extra="allow")

    monkeypatch.setattr(local_module, "LocalConfig", Keeps)
    toml = VALID_TOML.replace(
        'domain = "weizmann.ac.il"',
        # TOML literal strings ('...'): backslashes as an operator would type them.
        textwrap.dedent(
            r"""
            domain = "weizmann.ac.il"
            share = 'Z:\MAST'
            more = ['Z:/MAST/runs', 'C:\MAST\x']
            """
        ),
    )
    toml += textwrap.dedent(
        r"""
        [extra]
        night = 'Z:\MAST\mast00\2026-10-24\'
        """
    )
    _point_at(tmp_path, toml, monkeypatch)

    local = load_local_config()

    assert local.share == "//mast-ns-control/mast-share/MAST"
    assert local.more == ["//mast-ns-control/mast-share/MAST/runs", r"C:\MAST\x"]
    assert local.extra == {"night": "//mast-ns-control/mast-share/MAST/mast00/2026-10-24/"}
    assert local.controller_host == "mast-ns-control"  # the rest is untouched


def test_filer_share_root_is_z_through_the_same_function():
    from common.filer import SHARE_DRIVE_ROOT

    assert share_path(SHARE_DRIVE_ROOT, "mast-ns-control") == "//mast-ns-control/mast-share/MAST/"
