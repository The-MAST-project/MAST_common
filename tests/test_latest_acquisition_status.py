"""The latest acquisition's outcome, as FullUnitStatus publishes it (MAST_unit#326)."""

from __future__ import annotations

from common.models.statuses import FullUnitStatus, LatestAcquisition


def test_an_old_unit_reports_no_acquisition():
    status = FullUnitStatus.model_validate({"id": 1})

    assert status.latest_acquisition is None


def test_a_running_acquisition_has_no_outcome_yet():
    acquisition = LatestAcquisition(
        folder="Z:/MAST/mast01/2026-10-13/Acquisitions/seq=0001", target_ra_j2000_hours=5.5, target_dec_j2000_degs=-12.0
    )

    assert acquisition.succeeded is None
    assert acquisition.failure is None


def test_a_failed_acquisition_round_trips():
    payload = {
        "id": 1,
        "latest_acquisition": {
            "folder": "Z:/MAST/mast01/2026-10-13/Acquisitions/seq=0001",
            "target_ra_j2000_hours": 5.5,
            "target_dec_j2000_degs": -12.0,
            "succeeded": False,
            "failure": "the acquisition was cancelled",
        },
    }

    status = FullUnitStatus.model_validate_json(FullUnitStatus.model_validate(payload).model_dump_json())

    assert status.latest_acquisition is not None
    assert status.latest_acquisition.succeeded is False
    assert status.latest_acquisition.failure == "the acquisition was cancelled"
