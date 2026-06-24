"""Integration tests for the acquisition frame-transfer lifecycle (issue #18).

These drive the *real* ``Filer`` against temp directories and real background
threads -- no hardware, no astrometry.net, no Mongo. ``filer.py`` is
self-contained (its only platform import is ``win32api`` on Windows), so we add
its directory to ``sys.path`` and import it directly, the same way the solver
drift tests import ``pixel_grid``.

Each test is written to FAIL against the pre-fix ``Filer`` and PASS after it:

* write-safety -- ``move()`` must wait for a source that is still being written
  instead of racing it. The old code probed ``src.exists()`` once and bailed
  with "path does not exist" (dropping a frame that the RAM-disk wipe then loses
  forever), or moved a half-written file;
* audit trail -- every successful move is logged, and multi-file batches log a
  ``moved X/N`` reconciliation. The old success log was commented out, so there
  was no positive proof a frame reached the share;
* scratch cleanup -- ``clean_ram_tmp()`` removes solve-field ``<ram>/tmp/tmp_*``
  dirs. The method did not exist before, so those tests error on the old code.

Run from the unit repo root::

    pytest src/common/tests/test_frame_transfer.py -v
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import pytest

COMMON_DIR = Path(__file__).resolve().parents[1]  # .../src/common
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))

# On Windows filer.py imports win32api at load; skip cleanly if pywin32 is absent.
filer = pytest.importorskip("filer")


def _wait_until(predicate, timeout=15.0, poll=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


@pytest.fixture
def make_filer():
    """Factory for a Filer whose ram/shared/local roots are temp dirs.

    Bypasses ``Filer.__init__`` (which probes the real C:/D:/Z: drives via
    win32api) using ``__new__`` and wires only what the move lifecycle touches.
    Roots are stored posix-style with a trailing slash, matching the
    ``Path(...).as_posix()`` form ``move_ram_to_shared`` rewrites.
    """

    def _make(ram: Path, shared: Path, logger=None):
        ram.mkdir(parents=True, exist_ok=True)
        shared.mkdir(parents=True, exist_ok=True)
        f = filer.Filer.__new__(filer.Filer)
        f.logger = logger
        f.ram = filer.Location(None, ram.as_posix().rstrip("/") + "/")
        f.shared = filer.Location(None, shared.as_posix().rstrip("/") + "/")
        f.local = f.shared
        f.tops = {}
        return f

    return _make


# --- write-safety (P1) -------------------------------------------------------

def test_move_waits_for_a_file_created_late(make_filer, tmp_path):
    """move() must not give up when the producer hasn't created the file yet.

    Old ``move`` checked ``src.exists()`` once and bailed, so a frame still being
    exposed/flushed was never moved -- and lost on the next RAM-disk wipe.
    """
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    src = tmp_path / "ram" / "frame.fits"
    dst = tmp_path / "shared" / "frame.fits"
    payload = b"FITS" * 4096

    def slow_writer():
        time.sleep(1.0)  # producer is still working when move() is called
        src.write_bytes(payload)

    t = threading.Thread(target=slow_writer)
    t.start()
    try:
        moved = f.move(str(src), str(dst))  # synchronous; must wait for the writer
    finally:
        t.join()

    assert moved is True
    assert dst.read_bytes() == payload
    assert not src.exists()  # moved, not copied


def test_move_waits_for_a_growing_file(make_filer, tmp_path):
    """move() must wait until the writer stops growing the file.

    Old code moved as soon as the file existed, capturing a truncated frame (or
    failing the rename while the writer held the handle). The fix waits for the
    size to stabilize.
    """
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    src = tmp_path / "ram" / "frame.fits"
    dst = tmp_path / "shared" / "frame.fits"
    chunk = b"x" * 100_000
    n_chunks = 8

    def growing_writer():
        with src.open("wb") as fh:
            for _ in range(n_chunks):
                fh.write(chunk)
                fh.flush()
                time.sleep(0.2)

    t = threading.Thread(target=growing_writer)
    t.start()
    try:
        moved = f.move(str(src), str(dst))
    finally:
        t.join()

    assert moved is True
    assert dst.stat().st_size == n_chunks * len(chunk)  # complete, not partial


def test_wait_until_stable_gives_up_for_absent_file(tmp_path):
    """The write-safety gate reports failure (not a hang) for a missing file."""
    assert filer.Filer._wait_until_stable(
        tmp_path / "never.fits", timeout=0.5, poll=0.05
    ) is False


# --- audit trail / reconciliation (P2) ---------------------------------------

def test_successful_move_is_logged(make_filer, tmp_path, caplog):
    """Every successful move emits a positive audit line (old code: none)."""
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("mast.test.filer.single")
    f = make_filer(tmp_path / "ram", tmp_path / "shared", logger=logger)
    src = tmp_path / "ram" / "frame.fits"
    dst = tmp_path / "shared" / "frame.fits"
    src.write_bytes(b"data")

    assert f.move(str(src), str(dst)) is True
    moved_lines = [r.getMessage() for r in caplog.records if "moved" in r.getMessage()]
    assert any("frame.fits" in m for m in moved_lines), moved_lines


def test_batch_move_reports_reconciliation(make_filer, tmp_path, caplog):
    """move_ram_to_shared logs a moved-X/N count and lands every file.

    Old code spawned a thread per path with no success log and no reconciliation,
    so a dropped file was invisible.
    """
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("mast.test.filer.batch")
    ram, shared = tmp_path / "ram", tmp_path / "shared"
    f = make_filer(ram, shared, logger=logger)

    seq = ram / "2026-06-24" / "Acquisitions" / "seq=0001" / "spec"
    seq.mkdir(parents=True)
    (seq / "corrections.json").write_text("{}")
    (seq / "corrections.png").write_bytes(b"png")
    paths = [str(seq / "corrections.json"), str(seq / "corrections.png")]

    f.move_ram_to_shared(paths)

    dst_dir = shared / "2026-06-24" / "Acquisitions" / "seq=0001" / "spec"
    assert _wait_until(lambda: (dst_dir / "corrections.json").exists()
                       and (dst_dir / "corrections.png").exists())
    assert _wait_until(
        lambda: any("ram->shared: 2/2" in r.getMessage() for r in caplog.records)
    ), [r.getMessage() for r in caplog.records]
    assert not (seq / "corrections.json").exists()  # source RAM disk emptied


# --- scratch cleanup (P3) ----------------------------------------------------

def test_clean_ram_tmp_removes_only_solver_scratch(make_filer, tmp_path):
    """clean_ram_tmp() deletes <ram>/tmp/tmp_* but nothing else."""
    ram = tmp_path / "ram"
    f = make_filer(ram, tmp_path / "shared")

    tmp_root = ram / "tmp"
    (tmp_root / "tmp_AAAA").mkdir(parents=True)
    (tmp_root / "tmp_AAAA" / "axy.fits").write_bytes(b"scratch")
    (tmp_root / "tmp_BBBB").mkdir(parents=True)
    keep_dir = tmp_root / "mastrometry"          # not a tmp_* dir
    keep_dir.mkdir(parents=True)
    (keep_dir / "full-frame.fits").write_bytes(b"keep")
    product = ram / "2026-06-24" / "Exposures"   # a product tree, must survive
    product.mkdir(parents=True)
    (product / "frame.fits").write_bytes(b"product")

    f.clean_ram_tmp()

    assert not (tmp_root / "tmp_AAAA").exists()
    assert not (tmp_root / "tmp_BBBB").exists()
    assert (keep_dir / "full-frame.fits").read_bytes() == b"keep"
    assert (product / "frame.fits").read_bytes() == b"product"
