"""Tests for the cross-process folder claim and the relocation sweep (#56, #52 piece 3).

`MoveGuardian`'s registries are process-local, so a relocation sweep running elsewhere
could move a frame mid-write. A folder in use is therefore also claimed through a lock
file beside it -- shared for producers, exclusive for the sweeper.

The Windows behaviour these rely on was measured on mast00 (see #56): shared and exclusive
locks work across processes, a killed holder's lock is released while the file survives,
and a sibling lock leaves the folder movable. What is tested here is our own logic on top:
that a claim is taken and dropped at the right moments, and that the sweep relocates
exactly the folders nobody is using.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("common.filer", reason="filer import chain unavailable")
from common.filer import Filer, FilerTop, Location, MoveGuardian, _folder_lock_path


@pytest.fixture
def filer(tmp_path, monkeypatch):
    ram = tmp_path / "ram"
    shared = tmp_path / "shared"
    ram.mkdir()
    shared.mkdir()

    def fake_init(self, logger=None):
        # Posix-style, as production roots are on every platform ("D:/" + "MAST/").
        self.ram = Location(None, f"{ram.as_posix()}/")
        self.shared = Location(None, f"{shared.as_posix()}/")
        self.local = self.shared
        self.tops = {FilerTop.Local: self.local, FilerTop.Shared: self.shared, FilerTop.Ram: self.ram}
        self.logger = logger

    monkeypatch.setattr(Filer, "__init__", fake_init)
    with Filer._pending_lock:
        Filer._pending.clear()
        Filer._in_flight.clear()
    monkeypatch.setattr(Filer, "_mover_pool", None)
    monkeypatch.setattr(Filer, "_ensure_sweeper", lambda self: None)

    guardian = MoveGuardian()
    for real in list(MoveGuardian._folder_claims):
        guardian._release_claim(real)
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()

    instance = Filer()
    instance.ram_dir, instance.shared_dir = ram, shared
    yield instance

    for real in list(MoveGuardian._folder_claims):
        guardian._release_claim(real)


def _acquisition(ram, name="acq-0001", filename="e1.fits"):
    folder = ram / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text("payload")
    return folder


def test_lock_file_is_a_dotted_sibling(tmp_path):
    """Beside the folder, not inside it -- so it is neither copied with the folder nor
    able to stop its own holder from moving it."""
    folder = tmp_path / "daily" / "acq-0001"
    lock = _folder_lock_path(str(folder))

    assert os.path.dirname(lock) == os.path.dirname(str(folder))
    assert os.path.basename(lock) == ".acq-0001.lock"


def test_protect_claims_the_containing_folder(filer):
    folder = _acquisition(filer.ram_dir)
    guardian = MoveGuardian()

    with guardian.protect(str(folder / "e1.fits")):
        assert guardian.folder_is_claimed(str(folder))
        assert os.path.exists(_folder_lock_path(os.path.realpath(str(folder))))

    # Still claimed after the write: the gap between exposures must not look idle.
    assert guardian.folder_is_claimed(str(folder))


def test_release_folder_drops_the_claim_and_the_lock_file(filer):
    folder = _acquisition(filer.ram_dir)
    guardian = MoveGuardian()
    real = os.path.realpath(str(folder))

    with guardian.protect(str(folder / "e1.fits")):
        pass
    assert guardian.folder_is_claimed(str(folder))

    guardian._release_claim(real)

    assert not guardian.folder_is_claimed(str(folder))
    assert not os.path.exists(_folder_lock_path(real)), "the pair must disappear together"


def test_sweep_relocates_an_unclaimed_folder(filer):
    """The leftover case: a previous run died, nothing holds the folder."""
    folder = _acquisition(filer.ram_dir)

    filer._relocate_products()
    Filer._mover_pool.shutdown(wait=True)

    assert not folder.exists(), "an unclaimed leftover must be relocated"
    assert (filer.shared_dir / "acq-0001" / "e1.fits").read_text() == "payload"


def test_sweep_skips_a_claimed_folder(filer):
    """The dangerous case: another process is writing into it right now."""
    folder = _acquisition(filer.ram_dir)
    MoveGuardian().claim_folder(str(folder))

    filer._relocate_products()
    if Filer._mover_pool is not None:
        Filer._mover_pool.shutdown(wait=True)

    assert folder.exists(), "a folder in use must not be carried off"
    assert (folder / "e1.fits").exists()


def test_sweep_does_not_carry_a_claimed_folder_inside_its_parent(filer):
    """A claimed leaf must survive even when its parent looks idle -- otherwise the claim
    is worthless, since folders nest (`<date>/deepspec/acquisition-0001/<band>`)."""
    parent = filer.ram_dir / "2026-08-11"
    claimed = _acquisition(parent, "acq-0001")
    loose = _acquisition(parent, "acq-0002")
    MoveGuardian().claim_folder(str(claimed))

    filer._relocate_products()
    Filer._mover_pool.shutdown(wait=True)

    assert claimed.exists(), "claimed leaf must stay"
    assert not loose.exists(), "its unclaimed sibling must still be relocated"


def test_lock_files_are_not_themselves_relocated(filer):
    """A folder holding only lock files is not a unit of work."""
    folder = filer.ram_dir / "acq-0001"
    folder.mkdir()
    MoveGuardian().claim_folder(str(folder))  # writes ../.acq-0001.lock

    filer._relocate_products()

    assert not (filer.shared_dir / ".acq-0001.lock").exists()
    assert not any(p.name.endswith(".lock") for p in filer.shared_dir.rglob("*"))


def test_flush_returns_when_nothing_is_outstanding(filer):
    assert filer.flush(timeout=2.0) is True


def test_flush_reports_what_is_still_outstanding(filer):
    """At shutdown the caller needs to know a move never completed, not just that it
    waited -- that log line is the only trace an artifact was left behind."""
    with Filer._pending_lock:
        Filer._pending[str(filer.ram_dir / "stuck.fits")] = str(filer.shared_dir / "stuck.fits")

    assert filer.flush(timeout=0.5) is False
