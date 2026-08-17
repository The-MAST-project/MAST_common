"""Sequence counters and lock claims stay where they are.

`seq.txt` is PathMaker's per-folder counter, and both trees maintain their own, so moving it
is a guaranteed collision. `_merge_into` refused to overwrite -- correct for a product, wrong
for this -- and then could not remove the source folder, so it reported two errors and tried
again on the next sweep. For ever.

Observed on mast00, 2026-08-17: two errors per folder every 30 seconds, 19 files stranded on
the ram disk, and the counters diverged -- the ram side had reached 3 while the share still
said 1. `D:` is a RAM disk, so on reboot its counter is gone and numbering restarts at 0,
straight into folders already on the share. The durable copy that exists to prevent exactly
that was the one going stale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.filer import Filer, FilerTop, is_bookkeeping


@pytest.fixture
def filer(tmp_path):
    """A Filer whose ram and shared areas are two directories under tmp_path.

    Mirrors the fixture in test_move_merges_folders.py; kept local rather than shared so
    neither file's setup drifts under the other.
    """
    ram, shared = tmp_path / "ram", tmp_path / "shared"
    ram.mkdir()
    shared.mkdir()
    f = Filer()
    location = type("Location", (), {})
    f.ram = location()
    f.ram.root = ram.as_posix() + "/"
    f.shared = location()
    f.shared.root = shared.as_posix() + "/"
    f.local = f.shared
    f.tops = {FilerTop.Local: f.local, FilerTop.Shared: f.shared, FilerTop.Ram: f.ram}
    f.ram_path, f.shared_path = ram, shared
    return f


def write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("seq.txt", True),
        (".acq-0001.lock", True),
        (".Exposures.lock", True),
        ("reference.fits", False),
        ("result.json", False),
        ("seq.txt.bak", False),  # not the counter
        ("notes.lock", False),  # a claim is hidden; this is not one
        (".hidden", False),  # hidden, but not a claim
    ],
)
def test_what_counts_as_bookkeeping(name, expected):
    assert is_bookkeeping(name) is expected


class TestTheCounterStaysPut:
    def test_it_is_not_carried_across(self, filer):
        write(filer.ram_path / "Spirals" / "seq.txt", "3")
        write(filer.ram_path / "Spirals" / "0004" / "final.fits")
        write(filer.shared_path / "Spirals" / "seq.txt", "1")

        filer.move(filer.ram_path / "Spirals", filer.shared_path / "Spirals")

        assert (filer.shared_path / "Spirals" / "0004" / "final.fits").exists(), "products move"
        assert (filer.ram_path / "Spirals" / "seq.txt").read_text() == "3", "the ram counter is untouched"
        assert (filer.shared_path / "Spirals" / "seq.txt").read_text() == "1", "the share keeps its own"

    def test_it_stays_even_when_the_destination_has_none(self, filer):
        """The destination not existing used to route the move through `shutil.move`,
        which carried the whole tree -- counter included -- off the ram side mid-night."""
        write(filer.ram_path / "Spirals" / "seq.txt", "3")
        write(filer.ram_path / "Spirals" / "0004" / "final.fits")

        filer.move(filer.ram_path / "Spirals", filer.shared_path / "Spirals")

        assert (filer.shared_path / "Spirals" / "0004" / "final.fits").exists()
        assert (filer.ram_path / "Spirals" / "seq.txt").read_text() == "3"
        assert not (filer.shared_path / "Spirals" / "seq.txt").exists()

    def test_its_folder_survives_without_being_reported_as_a_failure(self, filer, caplog):
        """The source folder legitimately remains, holding the counter.

        Reporting that as "not empty after merging" is what flooded the log: it fires on
        every sweep of every folder that has a counter, which is all of them.
        """
        write(filer.ram_path / "Spirals" / "seq.txt", "3")
        write(filer.ram_path / "Spirals" / "0004" / "final.fits")

        with caplog.at_level("ERROR"):
            filer.move(filer.ram_path / "Spirals", filer.shared_path / "Spirals")

        assert (filer.ram_path / "Spirals").exists()
        assert not (filer.ram_path / "Spirals" / "0004").exists(), "the products still left"
        assert not [r for r in caplog.records if "not empty" in r.getMessage()], (
            "a folder kept for its counter is expected, not an error"
        )

    def test_a_counter_only_folder_is_not_a_relocation_candidate(self, filer, monkeypatch):
        """The sweeper picked such folders up and retried them for ever.

        A folder holding nothing but its own counter has no product to relocate.

        Asserted on what the sweep *dispatches*, not on the filesystem afterwards:
        `move_ram_to_shared` hands off to a thread, so a filesystem assertion here races
        the sweep and passes whatever the code does. That is exactly how an earlier version
        of this test missed the regression it was written for.
        """
        write(filer.ram_path / "Spirals" / "seq.txt", "3")
        write(filer.ram_path / "Acquisitions" / "0001" / "real.fits")

        dispatched: list[str] = []
        monkeypatch.setattr(type(filer), "move_ram_to_shared", lambda self, p: dispatched.append(str(p)))

        filer._relocate_products()

        assert not any("Spirals" in p for p in dispatched), "a counter-only folder has nothing to relocate"
        assert any("0001" in p for p in dispatched), "a folder with a product still is a candidate"


class TestClaimsStayPut:
    def test_a_claim_is_never_relocated(self, filer):
        write(filer.ram_path / "Spirals" / ".0004.lock")
        write(filer.ram_path / "Spirals" / "0004" / "final.fits")

        filer.move(filer.ram_path / "Spirals", filer.shared_path / "Spirals")

        assert (filer.ram_path / "Spirals" / ".0004.lock").exists(), "the claim is ram-local"
        assert not (filer.shared_path / "Spirals" / ".0004.lock").exists()
        assert (filer.shared_path / "Spirals" / "0004" / "final.fits").exists(), "the product still moves"
