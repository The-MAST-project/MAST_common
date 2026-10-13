"""`opmode` resolution: the environment, then this machine's configuration, then `development`."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from common.opmode import (
    DEFAULT_OPMODE,
    OPMODE_ENV,
    OpMode,
    OpState,
    opmode_from_env,
    resolve_opmode,
    resolve_opmode_with_source,
)


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

    @pytest.mark.parametrize("raw", ["production", " Production ", "PRODUCTION\n"])
    def test_case_and_whitespace_are_tolerated(self, monkeypatch, raw):
        monkeypatch.setenv(OPMODE_ENV, raw)
        assert opmode_from_env() is OpMode.PRODUCTION

    @pytest.mark.parametrize("raw", ["prodution", "controled", "", "tested"])
    def test_an_unrecognized_value_raises_naming_the_legal_ones(self, monkeypatch, raw):
        monkeypatch.setenv(OPMODE_ENV, raw)
        with pytest.raises(ValueError, match="development, production"):
            opmode_from_env()


class TestPreRenameNames:
    """`operated` / `controlled` (2026-10-06 .. 2026-10-12) are still read, with a WARNING.

    One shared config DB means the stored values change after the code, not with it.
    """

    @pytest.mark.parametrize(
        ("old", "new"),
        [("operated", OpMode.DEVELOPMENT), ("controlled", OpMode.PRODUCTION), (" Controlled\n", OpMode.PRODUCTION)],
    )
    def test_an_old_value_reads_as_its_replacement_with_a_warning(self, old, new, caplog):
        with caplog.at_level(logging.WARNING):
            assert OpMode(old) is new
        assert f"read as '{new.value}'" in caplog.text

    def test_an_old_env_value_is_accepted(self, monkeypatch):
        monkeypatch.setenv(OPMODE_ENV, "controlled")
        assert opmode_from_env() is OpMode.PRODUCTION

    def test_a_config_document_holding_an_old_value_still_validates(self):
        from common.config.unit import UnitConfig

        field = UnitConfig.model_fields["opmode"]
        from pydantic import TypeAdapter

        assert TypeAdapter(field.annotation).validate_python("controlled") is OpMode.PRODUCTION

    def test_the_old_member_names_are_aliases_not_extra_modes(self):
        assert OpMode.CONTROLLED is OpMode.PRODUCTION
        assert OpMode.OPERATED is OpMode.DEVELOPMENT
        assert list(OpMode) == [OpMode.DEVELOPMENT, OpMode.PRODUCTION]

    def test_the_old_property_names_still_answer(self):
        from common.opmode import OpmodeBase

        base = OpmodeBase()
        base._opmode = OpMode.PRODUCTION
        assert base.is_controlled is True
        assert base.is_operated is False

    def test_the_gui_offers_only_the_new_names(self):
        from common.config.opmode import opmode_field

        assert opmode_field().json_schema_extra["ui"]["options"] == ["development", "production"]


class TestResolve:
    def test_env_beats_configuration(self, monkeypatch):
        monkeypatch.setenv(OPMODE_ENV, "development")
        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.PRODUCTION))
        assert resolve_opmode() is OpMode.DEVELOPMENT

    def test_a_unit_reads_its_unit_configuration(self, monkeypatch, no_env):
        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.PRODUCTION))
        assert resolve_opmode() is OpMode.PRODUCTION

    def test_a_spec_reads_the_specs_configuration(self, monkeypatch, no_env):
        _machine(monkeypatch, "spec", specs=SimpleNamespace(opmode=OpMode.PRODUCTION))
        assert resolve_opmode() is OpMode.PRODUCTION

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


class TestSource:
    """The phrase naming where the mode came from, for the startup log line."""

    def test_env(self, monkeypatch):
        monkeypatch.setenv(OPMODE_ENV, "production")
        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.DEVELOPMENT))
        assert resolve_opmode_with_source() == (OpMode.PRODUCTION, OPMODE_ENV)

    def test_unit_configuration(self, monkeypatch, no_env):
        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.PRODUCTION))
        assert resolve_opmode_with_source() == (OpMode.PRODUCTION, "unit configuration")

    def test_specs_configuration(self, monkeypatch, no_env):
        _machine(monkeypatch, "spec", specs=SimpleNamespace(opmode=OpMode.PRODUCTION))
        assert resolve_opmode_with_source() == (OpMode.PRODUCTION, "specs configuration")

    def test_control_names_its_role(self, monkeypatch, no_env):
        _machine(monkeypatch, "control")
        assert resolve_opmode_with_source() == (DEFAULT_OPMODE, "default, machine_role control")

    def test_missing_unit_configuration(self, monkeypatch, no_env):
        _machine(monkeypatch, "unit", unit=None)
        assert resolve_opmode_with_source() == (DEFAULT_OPMODE, "default, no configuration for this unit")

    def test_opmode_base_caches_the_source_with_the_mode(self, monkeypatch, no_env):
        from common.opmode import OpmodeBase

        _machine(monkeypatch, "unit", unit=SimpleNamespace(opmode=OpMode.PRODUCTION))
        base = OpmodeBase()
        assert base.opmode_source == "unit configuration"
        assert base.opmode is OpMode.PRODUCTION

    def test_a_preset_mode_reports_preset(self):
        from common.opmode import OpmodeBase

        base = OpmodeBase()
        base._opmode = OpMode.DEVELOPMENT
        assert base.opmode_source == "preset"


class TestOpmodeBase:
    """The top component's mode and lifecycle state (opmode-design 4, 4a)."""

    def _base(self, mode):
        from common.opmode import OpmodeBase

        base = OpmodeBase()
        base._opmode = mode  # skip resolution: these tests are about the state, not the source
        return base

    def test_starts_initializing(self):
        assert self._base(OpMode.PRODUCTION).opstate is OpState.INITIALIZING

    def test_opstate_is_read_only_from_outside(self):
        """One writer: components used to assign the unit's opstate directly."""
        base = self._base(OpMode.PRODUCTION)
        with pytest.raises(AttributeError):
            base.opstate = OpState.STARTED_UP  # type: ignore[misc]

    def test_set_opstate_logs_a_change_and_not_a_repeat(self, caplog):
        base = self._base(OpMode.PRODUCTION)
        with caplog.at_level("INFO"):
            base._set_opstate(OpState.SHUT_DOWN)
            base._set_opstate(OpState.SHUT_DOWN)
        assert base.opstate is OpState.SHUT_DOWN
        assert sum("opstate" in r.getMessage() for r in caplog.records) == 1

    @pytest.mark.parametrize(
        ("mode", "development", "production"),
        [(OpMode.DEVELOPMENT, True, False), (OpMode.PRODUCTION, False, True)],
    )
    def test_is_development_and_is_production(self, mode, development, production):
        base = self._base(mode)
        assert base.is_development is development
        assert base.is_production is production
