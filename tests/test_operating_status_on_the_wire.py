"""`opmode` / `opstate` on `FullUnitStatus` (opmode-design 4, 6).

Optional and defaulting to None: `common` is one shared clone, so a control host has these
fields before any unit sends them, and must read an older unit's status as "not reported"
rather than as a lifecycle state the unit never claimed.
"""

from __future__ import annotations

import json

from common.models.statuses import FullUnitStatus
from common.opmode import OpMode, OpState


def _status(**fields) -> FullUnitStatus:
    return FullUnitStatus(id=1, powered=True, **fields)


def test_an_old_payload_without_the_fields_still_validates_as_none():
    status = FullUnitStatus.model_validate({"id": 1, "powered": True})
    assert status.opmode is None
    assert status.opstate is None


def test_the_fields_follow_activities_verbal_on_the_wire():
    fields = list(FullUnitStatus.model_fields)
    i = fields.index("activities_verbal")
    assert fields[i : i + 4] == ["activities_verbal", "was_shut_down", "opmode", "opstate"]


def test_they_serialise_as_the_bare_lower_case_literals():
    payload = json.loads(_status(opmode=OpMode.CONTROLLED, opstate=OpState.INITIALIZED).model_dump_json())
    assert payload["opmode"] == "controlled"
    assert payload["opstate"] == "initialized"


def test_a_stored_payload_round_trips():
    original = _status(opmode=OpMode.OPERATED, opstate=OpState.SHUTDOWN)
    again = FullUnitStatus.model_validate_json(original.model_dump_json())
    assert again.opmode is OpMode.OPERATED
    assert again.opstate is OpState.SHUTDOWN
