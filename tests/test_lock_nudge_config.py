"""The ``phd2.lock_nudge`` gate knobs, and the nudge's outcome in the unit status (MAST_unit#30).

The gate solves several guide frames and refuses the nudge when their estimates
disagree. Every new knob has a default: mast01's stored section is
``lock_nudge: {enabled: false}``, and a required field would fail that unit's config.
"""

from __future__ import annotations

import pytest

pytest.importorskip("common.config.phd2", reason="config package import chain unavailable")
from pydantic import ValidationError  # noqa: E402

from common.config.phd2 import LockNudgeConfig  # noqa: E402
from common.models.latest import AcquisitionState, LatestAcquisition, LatestRecords, LockNudgeOutcome  # noqa: E402
from common.models.statuses import FullUnitStatus  # noqa: E402


def test_a_stored_section_from_before_the_gate_still_parses_and_stays_off():
    conf = LockNudgeConfig.model_validate({"enabled": False})

    assert conf.enabled is False
    assert conf.solve_frames >= 1
    assert conf.max_spread_arcsec > 0
    assert conf.frames_timeout > 0


def test_the_nudge_is_off_by_default():
    assert LockNudgeConfig().enabled is False


@pytest.mark.parametrize(
    "knob",
    [{"solve_frames": 0}, {"max_spread_arcsec": 0.0}, {"max_spread_arcsec": -1.0}, {"frames_timeout": 0.0}],
)
def test_the_gate_knobs_must_be_positive(knob):
    with pytest.raises(ValidationError):
        LockNudgeConfig.model_validate(knob)


def test_the_nudge_outcome_round_trips_through_the_unit_status():
    nudge = LockNudgeOutcome(
        applied=False,
        reason='spread 1.40" exceeds max_spread_arcsec 1.0',
        offset_arcsec=0.62,
        offset_px=(-1.9, 1.1),
        spread_arcsec=1.4,
        frames_used=4,
    )
    acquisition = LatestAcquisition(state=AcquisitionState.GUIDING, nudge=nudge).begin()

    wire = FullUnitStatus(id=1, latest=LatestRecords(acquisition=acquisition)).model_dump(mode="json")
    back = FullUnitStatus.model_validate(wire)

    assert wire["latest"]["acquisition"]["nudge"]["applied"] is False
    assert back.latest is not None and back.latest.acquisition is not None
    assert back.latest.acquisition.nudge == nudge


def test_an_outcome_without_a_nudge_reads_as_not_reported():
    wire = LatestAcquisition(state=AcquisitionState.RUNNING).begin().model_dump(mode="json")
    del wire["nudge"]  # what an older unit sends

    assert LatestAcquisition.model_validate(wire).nudge is None
