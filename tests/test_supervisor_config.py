"""`SupervisorConfig` and the `opmode` field on the unit and spec configurations.

The property that matters most: both are fully defaulted, so documents that predate them
-- every `units` and `specs` document in the database today -- parse unchanged.
"""

from __future__ import annotations

import pytest

pytest.importorskip("common.config.unit", reason="config package import chain unavailable")

from common.config.specs import SpecsConfig  # noqa: E402
from common.config.supervisor import SupervisionMode, SupervisorConfig  # noqa: E402
from common.config.unit import UnitConfig  # noqa: E402
from common.opmode import OpMode  # noqa: E402


def test_the_bare_default_supervises_nothing():
    assert SupervisorConfig().processes == {}


def test_a_unit_supervises_pwi4_and_ps3cli_and_observes_phd2():
    modes = {name: proc.mode for name, proc in SupervisorConfig.for_unit().processes.items()}
    assert modes == {
        "pwi4": SupervisionMode.SUPERVISED,
        "phd2": SupervisionMode.OBSERVED,
        "ps3cli": SupervisionMode.SUPERVISED,
    }


@pytest.mark.parametrize("model", [UnitConfig, SpecsConfig])
def test_neither_new_field_is_required(model):
    assert not model.model_fields["opmode"].is_required()
    assert not model.model_fields["supervisor"].is_required()


@pytest.mark.parametrize("model", [UnitConfig, SpecsConfig])
def test_opmode_defaults_to_automatic(model):
    assert model.model_fields["opmode"].default is OpMode.AUTOMATIC


@pytest.mark.parametrize("model", [UnitConfig, SpecsConfig])
def test_the_opmode_selector_offers_exactly_the_modes(model):
    extra = model.model_fields["opmode"].json_schema_extra
    assert extra["ui"]["options"] == [mode.value for mode in OpMode]


def test_each_role_gets_its_own_supervisor_default():
    assert UnitConfig.model_fields["supervisor"].get_default(call_default_factory=True) == SupervisorConfig.for_unit()
    assert SpecsConfig.model_fields["supervisor"].get_default(call_default_factory=True) == SupervisorConfig()


def test_an_opmode_outside_the_modes_is_rejected():
    from pydantic import TypeAdapter, ValidationError

    with pytest.raises(ValidationError):
        TypeAdapter(OpMode).validate_python("tested")
