"""Integration tests for the acquisition frame-transfer lifecycle (issue #18).

These drive the *real* ``Filer`` against temp directories and real background
threads -- no hardware, no astrometry.net, no Mongo. ``filer.py`` is
self-contained (its only platform import is ``win32api`` on Windows), so we add
its directory to ``sys.path`` and import it directly, the same way the solver
drift tests import ``pixel_grid``.

Write-safety is an explicit contract, not a size-stability guess: a product is
written to ``<name>.part`` and atomically renamed to ``<name>`` only once the
writer closes (``Filer.atomic_path``), so a file under its final name is complete
by construction and ``*.part`` temps are never moved. The mover publishes to the
destination the same way, so a reader there never sees a partial either.

Each test is written to FAIL against the pre-``.part`` ``Filer`` (which had no
``atomic_path`` / size-stability gate) and PASS after it. Run from the unit repo
root::

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
PART = filer.PART_SUFFIX


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


# --- the atomic-write contract (P1) ------------------------------------------

def test_atomic_path_publishes_only_a_complete_file(make_filer, tmp_path):
    """A reader watching the final name never sees a partial.

    The producer writes through ``atomic_path`` over ~1.6 s; a concurrent reader
    polls the *final* name. The final name must only ever appear complete -- this
    is the robustness size-stability could not guarantee.
    """
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    final = tmp_path / "ram" / "frame.fits"
    chunk = b"x" * 100_000
    n_chunks = 8
    full = n_chunks * len(chunk)
    seen, stop = [], threading.Event()

    def reader():
        while not stop.is_set():
            if final.exists():
                seen.append(final.stat().st_size)
            time.sleep(0.03)

    def writer():
        with f.atomic_path(str(final)) as tmp:
            with open(tmp, "wb") as fh:
                for _ in range(n_chunks):
                    fh.write(chunk)
                    fh.flush()
                    time.sleep(0.2)

    r = threading.Thread(target=reader)
    r.start()
    try:
        writer()
        time.sleep(0.1)
    finally:
        stop.set()
        r.join()

    assert seen, "reader never observed the final file"
    assert all(s == full for s in seen), f"observed a partial final: {sorted(set(seen))}"
    assert not Path(str(final) + PART).exists()  # temp renamed away


def test_atomic_path_removes_temp_and_does_not_publish_on_error(make_filer, tmp_path):
    """If the writer raises, nothing appears under the final name and no temp leaks."""
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    final = tmp_path / "ram" / "frame.fits"

    with pytest.raises(RuntimeError):
        with f.atomic_path(str(final)) as tmp:
            Path(tmp).write_bytes(b"partial")
            raise RuntimeError("writer blew up mid-frame")

    assert not final.exists()                      # never published
    assert not Path(str(final) + PART).exists()    # temp cleaned up


def test_move_publishes_a_file_created_late(make_filer, tmp_path):
    """move() absorbs the ordering slack when the producer publishes slightly late."""
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    src = tmp_path / "ram" / "frame.fits"
    dst = tmp_path / "shared" / "frame.fits"
    payload = b"FITS" * 4096

    def slow_writer():
        time.sleep(1.0)
        with f.atomic_path(str(src)) as tmp:
            Path(tmp).write_bytes(payload)

    t = threading.Thread(target=slow_writer)
    t.start()
    try:
        moved = f.move(str(src), str(dst))  # must wait for the late publish
    finally:
        t.join()

    assert moved is True
    assert dst.read_bytes() == payload
    assert not src.exists()


def test_wait_for_path_returns_false_for_absent(tmp_path):
    """The existence gate reports failure (not a hang) for a missing file."""
    assert filer.Filer._wait_for_path(
        tmp_path / "never.fits", timeout=0.5, poll=0.05
    ) is False


# --- mover write-safety (P1) -------------------------------------------------

def test_move_skips_an_inflight_part_source(make_filer, tmp_path):
    """An in-flight '*.part' temp is never moved."""
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    src = tmp_path / "ram" / ("frame.fits" + PART)
    src.write_bytes(b"partial")
    dst = tmp_path / "shared" / ("frame.fits" + PART)

    assert f.move(str(src), str(dst)) is False
    assert not dst.exists()
    assert src.exists()  # left in place


def test_move_publishes_destination_without_leaving_a_part(make_filer, tmp_path):
    """The mover stages to <dst>.part then renames, leaving a complete dst and no temp."""
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    src = tmp_path / "ram" / "frame.fits"
    dst = tmp_path / "shared" / "frame.fits"
    payload = b"D" * 500_000
    src.write_bytes(payload)

    assert f.move(str(src), str(dst)) is True
    assert dst.read_bytes() == payload
    assert not Path(str(dst) + PART).exists()  # staged temp renamed away
    assert not src.exists()


def test_folder_move_skips_inflight_part_files(make_filer, tmp_path):
    """An end-of-activity folder move publishes finished files and ignores '*.part'."""
    f = make_filer(tmp_path / "ram", tmp_path / "shared")
    folder = tmp_path / "ram" / "Autofocus"
    folder.mkdir(parents=True)
    (folder / "vcurve.png").write_bytes(b"done")
    (folder / ("status.json" + PART)).write_bytes(b"inflight")
    dst = tmp_path / "shared" / "Autofocus"

    assert f.move(str(folder), str(dst)) is True
    assert (dst / "vcurve.png").read_bytes() == b"done"
    assert not (dst / ("status.json" + PART)).exists()  # in-flight temp not published
    assert not (dst / "status.json").exists()


# --- audit trail / reconciliation (P2) ---------------------------------------

def test_successful_move_is_logged(make_filer, tmp_path, caplog):
    """Every successful move emits a positive audit line."""
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
    """move_ram_to_shared logs a moved-X/N count and lands every file."""
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
