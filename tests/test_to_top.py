"""`Filer.to_top` / `area_of` / `locate`: re-rooting a path between filing areas.

plans/filing-areas-design.md 3, item 3. `to_top` replaces `change_top_to`, which needed a
forward-slashed input, replaced every occurrence of the root rather than the leading one,
returned None when nothing matched, and raised AttributeError on Linux's None RAM entry.

Pure string work, so the Windows, UNC and Linux spellings are all tested on every runner.
"""

from __future__ import annotations

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea, Location


def _filer(ram: str | None, shared: str, local: str) -> Filer:
    filer = object.__new__(Filer)
    filer.ram = Location(ram) if ram is not None else None
    filer.shared = Location(shared)
    filer.local = Location(local)
    return filer


@pytest.fixture
def unit():
    return _filer(ram="D:/MAST/", shared="//mast-ns-control/mast-share/MAST/mast00/", local="C:/MAST/")


@pytest.fixture
def control():
    return _filer(ram=None, shared="/Storage/mast-share/MAST/mast-ns-control/", local="/Storage/mast-share/MAST/")


class TestToTop:
    @pytest.mark.parametrize(
        "path",
        [
            "D:/MAST/2026-10-21/Acquisitions/seq=0001",
            "D:\\MAST\\2026-10-21\\Acquisitions\\seq=0001",
            "D:\\MAST/2026-10-21\\Acquisitions/seq=0001",
            "D://MAST//2026-10-21/Acquisitions//seq=0001/",
        ],
    )
    def test_ram_to_share_whatever_the_spelling(self, unit, path):
        assert (
            unit.to_top(FilingArea.SHARE, path)
            == "//mast-ns-control/mast-share/MAST/mast00/2026-10-21/Acquisitions/seq=0001"
        )

    def test_share_to_ram_from_a_backslashed_unc_path(self, unit):
        path = "\\\\mast-ns-control\\mast-share\\MAST\\mast00\\2026-10-21\\Exposures"
        assert unit.to_top(FilingArea.RAM, path) == "D:/MAST/2026-10-21/Exposures"

    def test_only_the_leading_root_is_replaced(self, unit):
        # change_top_to's str.replace rewrote every occurrence.
        path = "D:/MAST/notes/D:/MAST/copied"
        assert unit.to_top(FilingArea.LOCAL, path) == "C:/MAST/notes/D:/MAST/copied"

    def test_the_root_itself_maps_to_the_other_root(self, unit):
        assert unit.to_top(FilingArea.SHARE, "D:/MAST") == "//mast-ns-control/mast-share/MAST/mast00/"

    def test_a_name_that_merely_starts_like_the_root_is_not_under_it(self, unit):
        with pytest.raises(ValueError):
            unit.to_top(FilingArea.SHARE, "D:/MASTER/x")

    def test_a_path_under_no_area_raises(self, unit):
        with pytest.raises(ValueError):
            unit.to_top(FilingArea.SHARE, "E:/elsewhere/x")

    def test_case_is_ignored_where_the_file_system_ignores_it(self, unit, monkeypatch):
        monkeypatch.setattr(filer_module, "_CASE_INSENSITIVE", True)
        assert unit.to_top(FilingArea.LOCAL, "d:/mast/x") == "C:/MAST/x"

    def test_case_counts_where_it_does_not(self, unit, monkeypatch):
        monkeypatch.setattr(filer_module, "_CASE_INSENSITIVE", False)
        with pytest.raises(ValueError):
            unit.to_top(FilingArea.LOCAL, "d:/mast/x")


class TestOnLinux:
    def test_share_and_local_work_without_a_ram_area(self, control):
        path = "/Storage/mast-share/MAST/mast-ns-control/2026-10-21/x.log"
        assert control.area_of(path) == (FilingArea.SHARE, "2026-10-21/x.log")

    def test_the_longest_matching_root_wins(self, control):
        # The machine's folder is inside the share root, which is `local` here.
        path = "/Storage/mast-share/MAST/mast-ns-control/runs"
        assert control.area_of(path)[0] is FilingArea.SHARE
        assert control.area_of("/Storage/mast-share/MAST/plans")[0] is FilingArea.LOCAL

    def test_to_ram_raises_instead_of_attributeerror(self, control):
        with pytest.raises(ValueError, match="no RAM filing area"):
            control.to_top(FilingArea.RAM, "/Storage/mast-share/MAST/x")


class TestCollapsedRoots:
    def test_ram_and_local_on_one_folder_still_convert(self):
        # D: unmapped: ram falls back to C:/MAST/. Conversion stays defined.
        filer = _filer(ram="C:/MAST/", shared="//h/s/MAST/mast00/", local="C:/MAST/")
        assert filer.to_top(FilingArea.SHARE, "C:\\MAST\\a\\b") == "//h/s/MAST/mast00/a/b"


class TestLocate:
    def test_on_the_share_first(self, tmp_path):
        ram, share = tmp_path / "ram", tmp_path / "share"
        for root in (ram, share):
            (root / "n" / "seq=0001").mkdir(parents=True)
        filer = _filer(ram=str(ram), shared=str(share), local=str(tmp_path / "local"))

        found = filer.locate(f"{ram}/n/seq=0001")

        assert found == Location(str(share)).root + "n/seq=0001"

    def test_still_on_the_ram_disk(self, tmp_path):
        ram, share = tmp_path / "ram", tmp_path / "share"
        (ram / "n" / "f.fits").parent.mkdir(parents=True)
        (ram / "n" / "f.fits").write_bytes(b"x")
        share.mkdir()
        filer = _filer(ram=str(ram), shared=str(share), local=str(tmp_path / "local"))

        assert filer.locate(str(share / "n" / "f.fits")) == Location(str(ram)).root + "n/f.fits"

    def test_nowhere(self, tmp_path):
        filer = _filer(ram=str(tmp_path / "ram"), shared=str(tmp_path / "share"), local=str(tmp_path / "local"))
        assert filer.locate(str(tmp_path / "ram" / "gone.fits")) is None
