"""`opmode` resolution: the environment, then this machine's configuration, then `automatic`."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from common.opmode import DEFAULT_OPMODE, OPMODE_ENV, OpMode, opmode_from_env, resolve_opmode


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.delenv(OPMODE_ENV, raising=False)


def _machine(monkeypatch, role: str, *, unit=None, specs=None):
    """Point resolve_opmode's function-local imports at a fake role and a fake Config."""
    import common.config
    import common.config.local

    monkeypatch.setattr(common.config.local, "load_local_config", lambda: SimpleNamespace(machine_role=role))

    class FakeConfig:
        def get_unit(self):
            return unit

        def get_specs(self):
            return specs

    monkeypatch.setattr(common.config, "Config", FakeConfig)


class TestFromEnv:
    def test_unset_is_none(self, no_env):
        assert opmode_from_env() is None

    @pytest.mark.parametrize("raw", ["controlled", " Controlled ", "CONTROLLED\n"])
    def test_case_and_whitespace_are_tolerated(self, monkeypatch, raw):
        monkeypatch.setenv(OPMODE_ENV, raw)
        assert opmode_from_env() is OpMode.CONTROLLED

    @pytest.mark.parametrize("raw", ["controled", "", "tested"])
    def test_an_unrecognized_value_raises_naming_the_legal_ones(self, monkeypatch, raw):
        monkeypatch.setenv(OPMODE_ENV, raw)
        with pytest.raises(ValueError, match="automatic, controlled"):
            opmode_from_env()


class TestResolve:
    def test_env_beats_configuration(self, monkeypatch):
        monkeypatch.setenv(OPMODE_ENV, "automatic")
        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.CONTROLLED))
        assert resolve_opmode() is OpMode.AUTOMATIC

    def test_a_unit_reads_its_unit_configuration(self, monkeypatch, no_env):
        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.CONTROLLED))
        assert resolve_opmode() is OpMode.CONTROLLED

    def test_a_spec_reads_the_specs_configuration(self, monkeypatch, no_env):
        _machine(monkeypatch, "spec", specs=SimpleNamespace(opmode=OpMode.CONTROLLED))
        assert resolve_opmode() is OpMode.CONTROLLED

    def test_control_gets_the_default(self, monkeypatch, no_env):
        _machine(monkeypatch, "control")
        assert resolve_opmode() is DEFAULT_OPMODE

    def test_a_missing_unit_configuration_is_the_default_with_a_warning(self, monkeypatch, no_env, caplog):
        _machine(monkeypatch, "unit", unit=None)
        with caplog.at_level(logging.WARNING):
            assert resolve_opmode() is DEFAULT_OPMODE
        assert "no configuration for this unit" in caplog.text

    def test_an_unreadable_configuration_is_the_default_with_a_warning(self, monkeypatch, no_env, tmp_path, caplog):
        import common.config.local

        monkeypatch.setenv("MAST_CONFIG", str(tmp_path / "absent.toml"))
        common.config.local.load_local_config.cache_clear()
        with caplog.at_level(logging.WARNING):
            assert resolve_opmode() is DEFAULT_OPMODE
        assert "configuration unreadable" in caplog.text
        common.config.local.load_local_config.cache_clear()

    def test_a_bad_env_value_still_raises_rather_than_defaulting(self, monkeypatch):
        monkeypatch.setenv(OPMODE_ENV, "controled")
        with pytest.raises(ValueError):
            resolve_opmode()
