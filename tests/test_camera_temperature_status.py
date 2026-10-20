"""The spectrograph cameras' temperature on the wire, and `NewtonActivities.AdjustingTemperature`.

The status fields are optional and default to None: `common` is one shared clone, so a
control host has them before a spectrograph sends them, and must read an older spec's
status as "not reported".
"""

from __future__ import annotations

import json

from common.activities import NewtonActivities
from common.models.statuses import CameraTemperatureState, NewtonStatus, QHY600Status


def test_an_old_newton_payload_without_the_fields_still_validates_as_none():
    status = NewtonStatus.model_validate({"temperature": -60.0})
    assert status.temperature == -60.0
    assert status.temperature_state is None
    assert status.temperature_at is None


def test_an_old_qhy600_payload_without_the_fields_still_validates_as_none():
    status = QHY600Status.model_validate({"temperature": -10.0})
    assert status.current_set_point is None
    assert status.temperature_state is None
    assert status.temperature_at is None


def test_the_state_serialises_as_the_bare_literal():
    payload = json.loads(NewtonStatus(temperature_state=CameraTemperatureState.DRIFT).model_dump_json())
    assert payload["temperature_state"] == "drift"


def test_a_qhy600_payload_round_trips_with_a_fractional_set_point():
    original = QHY600Status(
        current_set_point=-12.5,
        temperature=-12.3,
        temperature_state=CameraTemperatureState.ADJUSTING,
        temperature_at="2026-10-20T08:00:00+00:00",
    )
    again = QHY600Status.model_validate_json(original.model_dump_json())
    assert again.current_set_point == -12.5
    assert again.temperature_state is CameraTemperatureState.ADJUSTING
    assert again.temperature_at == original.temperature_at


def test_adjusting_temperature_is_appended_and_renumbers_nothing():
    # The bitmask goes on the wire, so every member that existed keeps its value.
    assert NewtonActivities.Aborting == 1 << 9
    assert NewtonActivities.AdjustingTemperature == 1 << 10
    assert list(NewtonActivities)[-1] is NewtonActivities.AdjustingTemperature
