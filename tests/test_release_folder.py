"""Tests for MoveGuardian.release_folder -- reaping a ram-disk folder once it has drained.

The rule being pinned: a folder is removed only after every *product* under it has left,
where "product" means a path someone wrapped in ``MoveGuardian.protect``. Anything never
protected -- the ``seq.txt`` exposure counter, and any scratch a producer chose not to
declare -- is discarded with the folder.

Two properties matter more than the happy path and are tested hardest:

- a product still sitting on the ram disk must KEEP the folder, because the alternative is
  deleting an artifact that was never saved (which is exactly what happened to the solver
  outputs in MAST_unit#88's neighbourhood -- they were produced, never moved, and would
  have been destroyed nightly under a naive rule);
- protection is transient, so ``release_folder`` must rely on the durable record rather
  than on anything still being in ``_protected`` at reap time.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("common.filer", reason="filer import chain unavailable")
from common.filer import MoveGuardian

REAP_TIMEOUT = 5.0


@pytest.fixture(autouse=True)
def clean_registries():
    """The guardian is a process-wide singleton; keep tests from leaking into each other."""
    guardian = MoveGuardian()
    with MoveGuardian._condition:
        MoveGuardian._products.clear()
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
    yield guardian
    with MoveGuardian._condition:
        MoveGuardian._products.clear()
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "seq=0001,time=23-06-04_959,target=1.0,2.0" / "spec"
    d.mkdir(parents=True)
    return d


def write(path, text="x"):
    path.write_text(text)
    return path


def reap(guardian, folder, timeout=REAP_TIMEOUT):
    """Run the reaper synchronously -- the public method spawns a thread we cannot join."""
    guardian._reap_folder(os.path.realpath(str(folder)), None, timeout)


class TestDrained:
    def test_removes_folder_once_products_are_gone(self, clean_registries, folder):
        frame = folder / "seq=0001.fits"
        with clean_registries.protect(str(write(frame))):
            pass
        frame.unlink()  # the mover took it

        reap(clean_registries, folder)
        assert not folder.exists()

    def test_unprotected_files_die_with_the_folder(self, clean_registries, folder):
        product = folder / "seq=0001.fits"
        with clean_registries.protect(str(write(product))):
            pass
        product.unlink()
        write(folder / "seq.txt", "9")  # the exposure counter, never protected
        write(folder / "scratch.tmp")  # undeclared scratch

        reap(clean_registries, folder)
        assert not folder.exists()

    def test_folder_with_no_products_at_all_is_removed(self, clean_registries, folder):
        """An acquisition that produced nothing still leaves a directory and a seq.txt."""
        write(folder / "seq.txt", "0")
        reap(clean_registries, folder)
        assert not folder.exists()

    def test_product_records_are_dropped_after_reaping(self, clean_registries, folder):
        product = folder / "seq=0001.fits"
        with clean_registries.protect(str(write(product))):
            pass
        product.unlink()

        reap(clean_registries, folder)
        assert not clean_registries._products_under(os.path.realpath(str(folder)))


class TestRetained:
    """Every case here must KEEP the folder. Deleting an unsaved artifact is the one
    outcome this feature must never produce."""

    def test_product_still_on_the_ram_disk_keeps_the_folder(self, clean_registries, folder):
        stranded = folder / "seq=0001.fits"
        with clean_registries.protect(str(write(stranded))):
            pass
        # No unlink: the move never happened -- exactly the mastrometry case.

        reap(clean_registries, folder, timeout=0.5)
        assert folder.exists()
        assert stranded.exists()

    def test_write_in_progress_keeps_the_folder(self, clean_registries, folder):
        write(folder / "seq.txt", "1")
        being_written = folder / "seq=0002.fits"
        with clean_registries.protect(str(write(being_written))):
            # Still inside the protect block: a producer is mid-write.
            reap(clean_registries, folder, timeout=0.5)
            assert folder.exists()

    def test_move_in_progress_keeps_the_folder(self, clean_registries, folder):
        write(folder / "seq.txt", "1")
        with clean_registries.moving(str(folder / "seq=0003.fits")):
            reap(clean_registries, folder, timeout=0.5)
            assert folder.exists()

    def test_deferred_move_keeps_the_folder(self, clean_registries, folder):
        """A move waiting on an unreachable share must not have its source deleted."""
        from common.filer import Filer

        deferred = str(folder / "seq=0004.fits")
        write(folder / "seq=0004.fits")
        with Filer._pending_lock:
            Filer._pending[os.path.realpath(deferred)] = "Z:/somewhere"
        try:
            reap(clean_registries, folder, timeout=0.5)
            assert folder.exists()
        finally:
            with Filer._pending_lock:
                Filer._pending.pop(os.path.realpath(deferred), None)


class TestGiveUpMessage:
    """When a folder is kept, that log line is the only record of what was never
    evacuated -- so it has to name all of them, not just the first."""

    class Recorder:
        def __init__(self):
            self.errors: list[str] = []

        def info(self, message):
            pass

        def error(self, message):
            self.errors.append(str(message))

    def test_names_every_blocking_product(self, clean_registries, folder):
        recorder = self.Recorder()
        first = write(folder / "seq=0001.fits")
        second = write(folder / "seq=0002.fits")
        with clean_registries.protect(str(first), str(second)):
            pass
        # Neither was moved.

        clean_registries._reap_folder(os.path.realpath(str(folder)), recorder, 0.2)

        assert folder.exists(), "a folder with unmoved products must be kept"
        message = " ".join(recorder.errors)
        assert "giving up" in message
        assert "2 product(s) not yet moved" in message
        assert first.name in message and second.name in message

    def test_says_nothing_when_it_succeeds(self, clean_registries, folder):
        recorder = self.Recorder()
        product = write(folder / "seq=0001.fits")
        with clean_registries.protect(str(product)):
            pass
        product.unlink()

        clean_registries._reap_folder(os.path.realpath(str(folder)), recorder, 0.2)

        assert not folder.exists()
        assert not recorder.errors, f"clean reap should log no errors, got {recorder.errors}"


class TestProductRecord:
    def test_protect_records_durably(self, clean_registries, folder):
        """The record must outlive the protect block -- that is the whole point."""
        product = write(folder / "seq=0001.fits")
        real = os.path.realpath(str(product))
        with clean_registries.protect(str(product)):
            assert clean_registries.is_protected(str(product))
        assert not clean_registries.is_protected(str(product)), "protection should be transient"
        assert real in clean_registries._products, "product record should be durable"

    def test_moving_does_not_record_a_product(self, clean_registries, folder):
        path = str(folder / "seq=0001.fits")
        with clean_registries.moving(path):
            pass
        assert os.path.realpath(path) not in clean_registries._products

    def test_scoped_by_folder(self, clean_registries, tmp_path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        with clean_registries.protect(str(write(a / "one.fits"))), clean_registries.protect(str(write(b / "two.fits"))):
            pass
        assert len(clean_registries._products_under(os.path.realpath(str(a)))) == 1
        clean_registries.forget_products_under(a)
        assert not clean_registries._products_under(os.path.realpath(str(a)))
        assert clean_registries._products_under(os.path.realpath(str(b))), "other folders must be untouched"
