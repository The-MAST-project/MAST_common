"""The pieces MAST_control's multi-unit acquisition needs from `common`.

Design: mast-claude-config plans/control_acquisition_design.md, section 4.
"""

from __future__ import annotations

import pytest

from common.models.plans import DEFAULT_TIMEOUT_TO_GUIDING, Plan
from common.models.statuses import (
    COOLING_DOWN_REASON,
    AcquisitionOutcome,
    AcquisitionState,
    FullUnitStatus,
)
from common.parsers import parse_target


def _status(**fields) -> FullUnitStatus:
    return FullUnitStatus(id=1, **fields)


def test_outcome_round_trips_through_the_unit_status():
    outcome = AcquisitionOutcome(
        state=AcquisitionState.FAILED,
        errors=["phase sky did not converge"],
        started_at="2026-10-18T20:00:00+00:00",
        ended_at="2026-10-18T20:03:00+00:00",
    )

    wire = _status(acquisition=outcome).model_dump(mode="json")
    back = FullUnitStatus.model_validate(wire)

    assert wire["acquisition"]["state"] == "failed"
    assert back.acquisition == outcome


def test_a_unit_that_reports_no_acquisition_reads_as_not_reported():
    wire = _status().model_dump(mode="json")
    del wire["acquisition"]  # what an older unit sends

    assert FullUnitStatus.model_validate(wire).acquisition is None


def test_outcome_errors_default_to_a_fresh_list():
    first = AcquisitionOutcome(state=AcquisitionState.RUNNING, started_at="t")
    second = AcquisitionOutcome(state=AcquisitionState.RUNNING, started_at="t")

    first.errors.append("x")

    assert second.errors == []


def test_plans_default_to_the_shared_timeout_to_guiding():
    assert Plan.model_fields["timeout_to_guiding"].default == DEFAULT_TIMEOUT_TO_GUIDING == 600


def test_the_cooling_reason_matches_what_units_already_send():
    # Pinned, not merely shared: units deployed before they built their messages from this
    # constant send the literal text, and control must still recognise it.
    assert COOLING_DOWN_REASON == "imager: cooling down"


@pytest.mark.parametrize(
    ("ra", "dec", "expected"),
    [
        ("5.575", "-45.5", (5.575, -45.5)),
        (5.575, -45.5, (5.575, -45.5)),
        ("05:34:30", "+22:00:36", (5.575, 22.01)),
        (" 05 34 30 ", "22 00 36", (5.575, 22.01)),
        ("0", "0", (0.0, 0.0)),
        (0, 0, (0.0, 0.0)),
    ],
)
def test_parse_target_takes_every_accepted_form(ra, dec, expected):
    assert parse_target(ra, dec) == pytest.approx(expected)


@pytest.mark.parametrize("missing", [None, ""])
def test_parse_target_passes_a_missing_coordinate_through(missing):
    assert parse_target(missing, missing) == (None, None)


@pytest.mark.parametrize(("ra", "dec"), [("24", "0"), ("0", "91"), ("abc", "0")])
def test_parse_target_refuses_what_is_out_of_range_or_unparseable(ra, dec):
    with pytest.raises(ValueError):
        parse_target(ra, dec)
