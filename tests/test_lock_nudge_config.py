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
from common.models.latest import (  # noqa: E402
    AcquisitionState,
    LatestAcquisition,
    LatestRecords,
    LockNudgeOutcome,
    NudgeSkyVerdict,
)
from common.models.statuses import FullUnitStatus, PointingSample, PointingSampleOutcome  # noqa: E402


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


# MAST_unit#401: the sky verdict and the primary's own offset


def test_the_sky_verdict_knobs_default_to_two_samples_under_half_an_arcsecond():
    conf = LockNudgeConfig.model_validate({"enabled": False})

    assert conf.verify_samples == 2
    assert conf.verify_arcsec == 0.5


@pytest.mark.parametrize("knob", [{"verify_samples": 0}, {"verify_arcsec": 0.0}, {"verify_arcsec": -0.5}])
def test_the_sky_verdict_knobs_must_be_positive(knob):
    with pytest.raises(ValidationError):
        LockNudgeConfig.model_validate(knob)


def test_the_sky_verdict_round_trips_through_the_unit_status():
    nudge = LockNudgeOutcome(
        applied=True,
        reason="nudged",
        residual_px=0.4,
        residual_primary_px=4.1,
        lock_moved_at="2026-11-04T20:15:15.104Z",
        confirmed_at="2026-11-04T20:15:52.728Z",
        sky_verdict=NudgeSkyVerdict.Unconfirmed,
        sky_offset_arcsec=1.21,
    )
    acquisition = LatestAcquisition(state=AcquisitionState.GUIDING, nudge=nudge).begin()

    wire = FullUnitStatus(id=1, latest=LatestRecords(acquisition=acquisition)).model_dump(mode="json")
    back = FullUnitStatus.model_validate(wire)

    assert wire["latest"]["acquisition"]["nudge"]["sky_verdict"] == "Unconfirmed"
    assert back.latest is not None and back.latest.acquisition is not None
    assert back.latest.acquisition.nudge == nudge


def test_an_outcome_from_before_the_sky_verdict_still_parses():
    wire = {"applied": True, "reason": "nudged", "residual_px": 0.7}  # what an older unit sends

    outcome = LockNudgeOutcome.model_validate(wire)

    assert outcome.sky_verdict is None
    assert outcome.residual_primary_px is None
    assert outcome.confirmed_at is None


def test_a_pointing_sample_carries_the_guide_step_offsets_when_known():
    sample = PointingSample(
        time="2026-11-04T20:16:12Z",
        outcome=PointingSampleOutcome.Solved,
        primary_offset_px=(-4.06, -1.03),
        guide_offset_px=(1.76, 4.56),
        guide_step_paired=True,
    )

    back = PointingSample.model_validate(sample.model_dump(mode="json"))

    assert back == sample
    assert PointingSample(time="t", outcome=PointingSampleOutcome.Unavailable).primary_offset_px is None
