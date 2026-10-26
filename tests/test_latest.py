"""The unit's latest-run records: one shape, `FullUnitStatus.latest.<kind>` (common.models.latest)."""

from __future__ import annotations

import json
import os

from common.filer import Filer, FilingArea, MoveGuardian
from common.models.latest import (
    LATEST_FILE,
    AcquisitionState,
    LatestAcquisition,
    LatestAcquisitionPhase,
    LatestExposure,
    LatestFocusOnStar,
    LatestRecords,
)
from common.models.statuses import AcquisitionState as ReExported
from common.models.statuses import FullUnitStatus


def _status(**fields) -> FullUnitStatus:
    return FullUnitStatus(id=1, **fields)


class TestTheBase:
    def test_no_run_yet(self):
        record = LatestExposure()
        assert record.started is None and not record.running and record.succeeded is None

    def test_begun_it_runs_finished_it_does_not(self):
        record = LatestExposure().begin(plan_id="01PLAN")
        assert record.running and record.plan_id == "01PLAN"
        record.finish(False, ["camera lost"])
        assert not record.running and record.succeeded is False and record.errors == ["camera lost"]

    def test_finishing_twice_changes_nothing(self):
        record = LatestExposure().begin()
        record.finish(True)
        ended = record.ended
        record.finish(False, ["late"])
        assert record.ended == ended and record.succeeded is True and record.errors == []

    def test_errors_are_a_fresh_list_each(self):
        first, second = LatestExposure(), LatestExposure()
        first.errors.append("x")
        assert second.errors == []

    def test_the_products_paths_are_known_when_it_starts(self):
        folder = os.path.join(Filer().ram.root, "2026-10-26", "Exposures")
        record = LatestExposure().begin(folder)
        assert record.local_products_path == folder.replace("\\", "/")
        assert record.shared_products_path == Filer().to_top(FilingArea.SHARE, folder)

    def test_a_folder_under_no_filing_area_has_no_share_path(self, tmp_path):
        record = LatestExposure().begin(tmp_path)
        assert record.local_products_path and record.shared_products_path is None


class TestWritingIt:
    def test_into_its_folder_as_a_product_and_to_the_mover(self, tmp_path, monkeypatch):
        moved: list[str] = []
        monkeypatch.setattr(Filer, "move_ram_to_shared", lambda self, p: moved.append(p))
        record = LatestFocusOnStar(outcome="solved", best_position=31000).begin(tmp_path / "0003")
        record.finish(True)

        path = record.write()

        assert path == os.path.join(str(tmp_path / "0003").replace("\\", "/"), LATEST_FILE)
        with open(path, encoding="utf-8") as f:
            written = json.load(f)
        assert written["outcome"] == "solved" and written["succeeded"] is True and written["running"] is False
        assert moved == [path]
        assert os.path.realpath(path) in MoveGuardian._products

    def test_without_a_folder_nothing(self):
        assert LatestExposure().begin().write() is None


class TestTheAcquisition:
    def test_a_phase_says_whether_it_ended_within_tolerance(self):
        assert LatestAcquisitionPhase(
            steps=3, tolerance_ra=2, tolerance_dec=2, last_ra_delta=1.5, last_dec_delta=-0.4
        ).within_tolerance
        assert not LatestAcquisitionPhase(
            steps=9, tolerance_ra=2, tolerance_dec=2, last_ra_delta=3, last_dec_delta=0
        ).within_tolerance
        assert LatestAcquisitionPhase().within_tolerance is None

    def test_the_state_is_re_exported_from_statuses(self):
        assert ReExported is AcquisitionState


class TestInTheStatus:
    def test_round_trips(self):
        latest = LatestRecords(
            acquisition=LatestAcquisition(state=AcquisitionState.FAILED, errors=["sky: no solution"]).begin()
        )
        wire = _status(latest=latest).model_dump(mode="json")
        back = FullUnitStatus.model_validate(wire)
        assert wire["latest"]["acquisition"]["state"] == "failed"
        assert back.latest.acquisition.errors == ["sky: no solution"]
        assert back.latest.focus_on_star is None and back.latest.exposure is None

    def test_a_unit_that_reports_no_latest_reads_as_not_reported(self):
        wire = _status().model_dump(mode="json")
        del wire["latest"]
        assert FullUnitStatus.model_validate(wire).latest is None
