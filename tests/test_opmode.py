"""`opmode` resolution: the environment, then this machine's configuration, then `operated`."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from common.opmode import DEFAULT_OPMODE, OPMODE_ENV, OpMode, OpState, opmode_from_env, resolve_opmode


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
        with pytest.raises(ValueError, match="operated, controlled"):
            opmode_from_env()


class TestResolve:
    def test_env_beats_configuration(self, monkeypatch):
        monkeypatch.setenv(OPMODE_ENV, "operated")
        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.CONTROLLED))
        assert resolve_opmode() is OpMode.OPERATED

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


class TestOpmodeBase:
    """The top component's mode and lifecycle state (opmode-design 4, 4a)."""

    def _base(self, mode):
        from common.opmode import OpmodeBase

        base = OpmodeBase()
        base._opmode = mode  # skip resolution: these tests are about the state, not the source
        return base

    def test_starts_initializing(self):
        assert self._base(OpMode.CONTROLLED).opstate is OpState.INITIALIZING

    def test_opstate_is_read_only_from_outside(self):
        """One writer: components used to assign the unit's opstate directly."""
        base = self._base(OpMode.CONTROLLED)
        with pytest.raises(AttributeError):
            base.opstate = OpState.RUNNING  # type: ignore[misc]

    def test_set_opstate_logs_a_change_and_not_a_repeat(self, caplog):
        base = self._base(OpMode.CONTROLLED)
        with caplog.at_level("INFO"):
            base._set_opstate(OpState.SHUTDOWN)
            base._set_opstate(OpState.SHUTDOWN)
        assert base.opstate is OpState.SHUTDOWN
        assert sum("opstate" in r.getMessage() for r in caplog.records) == 1

    @pytest.mark.parametrize(
        ("mode", "operated", "controlled"),
        [(OpMode.OPERATED, True, False), (OpMode.CONTROLLED, False, True)],
    )
    def test_is_operated_and_is_controlled(self, mode, operated, controlled):
        base = self._base(mode)
        assert base.is_operated is operated
        assert base.is_controlled is controlled
