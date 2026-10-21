"""`PathMaker.make_seq` seeds a counter that missed numbers (plans/filing-areas-design.md 7.2).

A revived RAM disk is empty -- no `seq.txt` -- so the next acquisition was `seq=0001` again,
straight into folders already on the share. A counter that is missing, or older than the RAM
area's last switch between disk and fallback, is now seeded from the highest number in the
same folder on every root. Otherwise it stays a file read.
"""

from __future__ import annotations

import errno
import os
import re

import pytest

import common.filer as filer_module
import common.paths as paths_module
from common.filer import Filer, FilingArea
from common.paths import PathMaker, _sequence_of
from common.testing.filer import reset_filing_areas


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 3600.0)
    reset_filing_areas()
    yield
    reset_filing_areas()


@pytest.fixture
def night(request):
    """A folder of this test's own: the suite's roots are shared by the whole session."""
    return re.sub(r"\W", "_", request.node.name)


@pytest.fixture
def roots():
    f = Filer()
    return {
        "ram": f.ram.root,
        "ram_fallback": f.ram_fallback.root,
        "share": f.shared.root,
        "share_fallback": f.share_fallback.root,
    }


def _numbered(root: str, relative: str, *names: str) -> None:
    for name in names:
        os.makedirs(os.path.join(root, relative, name), exist_ok=True)


class TestTheNames:
    @pytest.mark.parametrize(
        "name, number",
        [
            ("0007", 7),
            ("seq=0007,time=10-11-12_345,target=12.5", 7),
            ("run-0012", 12),
            ("acquisition-0003", 3),
            ("step-0004.fits", 4),
            ("single_exposure_0005.fits", 5),
            ("seq=0010.fits", 10),
        ],
    )
    def test_numbered(self, name, number):
        assert _sequence_of(name) == number

    @pytest.mark.parametrize("name", ["2026-10-21", "seq.txt", ".seq=0001.lock", "frame.fits", "Exposures", "007"])
    def test_not_numbered(self, name):
        assert _sequence_of(name) is None


class TestSeeding:
    def test_a_revived_ram_disk_continues_from_the_share(self, roots, night):
        rel = f"{night}/Acquisitions"
        _numbered(roots["share"], rel, "seq=0005,time=a", "seq=0006,time=b")

        assert PathMaker.make_seq(roots["ram"] + rel) == "0007"
        assert PathMaker.make_seq(roots["ram"] + rel) == "0008", "and counts on from there"

    def test_a_fresh_folder_on_the_ram_fallback_continues_from_the_disk_and_the_share(self, roots, night):
        rel = f"{night}/Autofocus"
        _numbered(roots["ram"], rel, "0003")
        _numbered(roots["share"], rel, "0002")

        assert PathMaker.make_seq(roots["ram_fallback"] + rel) == "0004"

    def test_the_share_fallback_counts_too(self, roots, night):
        rel = f"{night}/Calibration/Focuser"
        _numbered(roots["share_fallback"], rel, "0009")

        assert PathMaker.make_seq(roots["ram"] + rel) == "0010"

    def test_a_counter_older_than_a_ram_switch_is_seeded_again(self, roots, night):
        rel = f"{night}/Spirals"
        assert PathMaker.make_seq(roots["ram"] + rel) == "0001"
        # D: full, numbering went on on the fallback, those products went to the share.
        _numbered(roots["share"], rel, "0002", "0003", "0004")
        Filer().report_failure(FilingArea.RAM, "x", OSError(errno.ENOSPC, "full"))
        assert Filer().check_now(FilingArea.RAM)

        assert PathMaker.make_seq(roots["ram"] + rel) == "0005"

    def test_a_current_counter_is_a_file_read(self, roots, night, monkeypatch):
        rel = f"{night}/FluxMetering"
        PathMaker.make_seq(roots["ram"] + rel)
        monkeypatch.setattr(paths_module, "_highest_sequence_elsewhere", lambda folder: pytest.fail("listed the roots"))

        assert PathMaker.make_seq(roots["ram"] + rel) == "0002"

    def test_an_unreadable_share_is_not_listed(self, roots, night, monkeypatch):
        rel = f"{night}/Acquisitions"
        _numbered(roots["share"], rel, "seq=0050,time=a")
        monkeypatch.setattr(filer_module.Location, "readable", lambda self, timeout=2.0, fresh=False: False)

        assert PathMaker.make_seq(roots["ram"] + rel) == "0001"

    def test_a_folder_in_no_area_numbers_as_before(self, tmp_path):
        assert PathMaker.make_seq(str(tmp_path / "anywhere")) == "0001"
        assert PathMaker.make_seq(str(tmp_path / "anywhere"), start_with=10) == "0002"

    def test_start_with_is_a_floor_not_a_ceiling(self, roots, night):
        rel = f"{night}/Guidings"
        _numbered(roots["share"], rel, "0020")

        assert PathMaker.make_seq(roots["ram"] + rel, start_with=5) == "0021"
