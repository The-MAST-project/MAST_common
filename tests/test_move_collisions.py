"""A name collision is resolved or reported once, never re-reported every sweep (#117).

On mast01 over 2026-09-14/15, 2,090 of the night's 2,113 filer ERROR records were 40
files re-reported on every 30 s sweep for 28 minutes. Each came with a second ERROR for
its folder ("not empty after merging"). Every one was a byte-identical copy: a manual
robocopy had copied, rather than moved, frames the mover never got (MAST_unit#272).

- An **identical** copy at the destination means the move already happened. The source
  is dropped and nothing is reported as an error.
- A **different** file of the same name is a real conflict. The source stays, as it
  always did, but it is reported once, and a person resolving it lets the next sweep
  finish the move.
- A source that is already gone is ignored, and says so below ERROR.
"""

from __future__ import annotations

import filecmp
import logging
import os

import pytest
from test_move_merges_folders import write

from common import filer as filer_module
from common.filer import Filer, FilerTop

SWEEPS = 3


@pytest.fixture
def filer(tmp_path, monkeypatch):
    """A Filer with ram and shared areas under tmp_path, logging to a named logger."""
    ram, shared = tmp_path / "ram", tmp_path / "shared"
    ram.mkdir()
    shared.mkdir()
    f = Filer(logging.getLogger("test_move_collisions"))
    location = type("Location", (), {})
    f.ram = location()
    f.ram.root = ram.as_posix() + "/"
    f.shared = location()
    f.shared.root = shared.as_posix() + "/"
    f.local = f.shared
    f.tops = {FilerTop.Local: f.local, FilerTop.Shared: f.shared, FilerTop.Ram: f.ram}
    f.ram_path, f.shared_path = ram, shared
    monkeypatch.setattr(Filer, "_reported_collisions", set())
    return f


def errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def sweep(filer, folder: str, times: int = SWEEPS) -> None:
    """What the sweeper does to a blocked folder: try the same move again, every pass."""
    for _ in range(times):
        filer.move(filer.ram_path / folder, filer.shared_path / folder)


class TestAnIdenticalCopyIsNotACollision:
    def test_the_source_is_dropped_and_the_destination_kept(self, filer, caplog):
        write(filer.ram_path / "Autofocus" / "0002" / "FOCUS24850.fits", "frame")
        write(filer.shared_path / "Autofocus" / "0002" / "FOCUS24850.fits", "frame")

        sweep(filer, "Autofocus", times=1)

        assert (filer.shared_path / "Autofocus" / "0002" / "FOCUS24850.fits").read_text() == "frame"
        assert not (filer.ram_path / "Autofocus" / "0002").exists(), "the folder drains, so the ram disk is reclaimed"
        assert errors(caplog) == []

    def test_the_2026_09_14_shape_drains_in_one_sweep(self, filer, caplog):
        """Six folders, every file already copied to the share, beside files that were not."""
        for seq in ["0002", "0003", "0004", "0006", "0009", "0010"]:
            for name in ["FOCUS24850.fits", "FOCUS24900.fits", "status.json"]:
                write(filer.ram_path / "Autofocus" / seq / name, f"{seq}/{name}")
                write(filer.shared_path / "Autofocus" / seq / name, f"{seq}/{name}")
        write(filer.ram_path / "Autofocus" / "0011" / "FOCUS25000.fits", "not yet copied")

        sweep(filer, "Autofocus", times=1)

        assert [p for p in (filer.ram_path / "Autofocus").rglob("*") if p.is_file()] == []
        assert (filer.shared_path / "Autofocus" / "0011" / "FOCUS25000.fits").read_text() == "not yet copied"
        assert errors(caplog) == []


class TestARealCollisionIsReportedOnce:
    def test_both_files_survive(self, filer):
        write(filer.ram_path / "Autofocus" / "0001" / "FOCUS25000.fits", "tonight")
        write(filer.shared_path / "Autofocus" / "0001" / "FOCUS25000.fits", "before the reboot")

        sweep(filer, "Autofocus")

        assert (filer.ram_path / "Autofocus" / "0001" / "FOCUS25000.fits").read_text() == "tonight"
        assert (filer.shared_path / "Autofocus" / "0001" / "FOCUS25000.fits").read_text() == "before the reboot"

    def test_one_error_across_every_sweep(self, filer, caplog):
        write(filer.ram_path / "Autofocus" / "0001" / "FOCUS25000.fits", "tonight")
        write(filer.shared_path / "Autofocus" / "0001" / "FOCUS25000.fits", "before the reboot")

        sweep(filer, "Autofocus")

        [message] = errors(caplog)
        assert "FOCUS25000.fits" in message

    def test_a_known_difference_is_not_read_again_on_later_sweeps(self, filer, monkeypatch):
        """Each comparison reads both frames in full; a conflict nobody has touched is
        not worth re-reading every 30 s."""
        write(filer.ram_path / "Autofocus" / "0001" / "FOCUS25000.fits", "tonight")
        write(filer.shared_path / "Autofocus" / "0001" / "FOCUS25000.fits", "earlier")
        compared = []
        same_bytes = filer_module._same_bytes
        monkeypatch.setattr(filer_module, "_same_bytes", lambda a, b: compared.append(a) or same_bytes(a, b))

        sweep(filer, "Autofocus")

        assert len(compared) == 1

    def test_the_held_folder_is_not_reported_as_not_empty(self, filer, caplog):
        """The second ERROR per folder per sweep on the night: a consequence of the
        collision already reported, not a separate failure."""
        write(filer.ram_path / "Autofocus" / "0001" / "FOCUS25000.fits", "tonight")
        write(filer.shared_path / "Autofocus" / "0001" / "FOCUS25000.fits", "before the reboot")

        sweep(filer, "Autofocus")

        assert not [m for m in errors(caplog) if "not empty" in m]

    def test_each_distinct_collision_is_reported(self, filer, caplog):
        for name in ["a.fits", "b.fits"]:
            write(filer.ram_path / "spec" / name, "ram")
            write(filer.shared_path / "spec" / name, "shared")

        sweep(filer, "spec")

        assert len(errors(caplog)) == 2

    def test_resolving_it_lets_the_next_sweep_finish(self, filer, caplog):
        write(filer.ram_path / "spec" / "same.fits", "ram")
        write(filer.shared_path / "spec" / "same.fits", "shared")
        sweep(filer, "spec")

        (filer.shared_path / "spec" / "same.fits").unlink()
        sweep(filer, "spec", times=1)

        assert (filer.shared_path / "spec" / "same.fits").read_text() == "ram"
        assert not (filer.ram_path / "spec").exists()

    def test_the_same_name_colliding_again_later_is_reported_again(self, filer, caplog):
        """Once resolved, a later collision under the same path is a new event."""
        write(filer.ram_path / "spec" / "same.fits", "ram")
        write(filer.shared_path / "spec" / "same.fits", "shared")
        sweep(filer, "spec")
        (filer.shared_path / "spec" / "same.fits").unlink()
        sweep(filer, "spec", times=1)

        write(filer.ram_path / "spec" / "same.fits", "ram, again")
        sweep(filer, "spec")

        assert len(errors(caplog)) == 2


class TestAnEarlierComparisonIsNotReused:
    def test_a_destination_changed_without_a_new_mtime_is_compared_again(self, filer):
        """filecmp caches by path, size and mtime, even with shallow=False. On mast01 on
        2026-10-04 a pair compared equal once, a byte of the share copy then changed with
        its mtime unchanged, and the cached verdict deleted the only ram original."""
        source = filer.ram_path / "Autofocus" / "0001" / "FOCUS24673.fits"
        target = filer.shared_path / "Autofocus" / "0001" / "FOCUS24673.fits"
        write(source, "frame")
        write(target, "frame")
        assert filecmp.cmp(source, target, shallow=False)

        mtime = os.stat(target).st_mtime_ns
        target.write_text("framf")
        os.utime(target, ns=(mtime, mtime))

        sweep(filer, "Autofocus", times=1)

        assert source.read_text() == "frame"
        assert target.read_text() == "framf"


class TestAMissingSourceIsNotAnError:
    def test_it_is_a_warning(self, filer, caplog):
        filer.move(filer.ram_path / "corrections.json", filer.shared_path / "corrections.json")

        assert errors(caplog) == []
        assert [r for r in caplog.records if r.levelno == logging.WARNING and "does not exist" in r.getMessage()]
