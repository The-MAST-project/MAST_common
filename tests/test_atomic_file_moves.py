"""A moved file appears whole under its name, or not at all (plans/filing-areas-design.md 6.2).

A cross-volume `shutil.move` copied under the final name, so a share dropping mid-copy left
a truncated file there, which the next attempt read as a collision. Across volumes a file is
now copied to `.<name>.partial`, renamed into place, and only then removed from its source.

A rename across volumes is simulated by making the first rename refuse with EXDEV (or
Windows' ERROR_NOT_SAME_DEVICE), as it does between D: and the share.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil

import pytest

import common.filer as filer_module
from common.filer import Filer, Location, is_bookkeeping


@pytest.fixture
def filer(tmp_path):
    for name in ("ram", "shared", "local"):
        (tmp_path / name).mkdir()
    f = object.__new__(Filer)
    f.ram, f.shared, f.local = (Location(str(tmp_path / n)) for n in ("ram", "shared", "local"))
    f.share_root = f.shared
    f.logger = logging.getLogger("test.atomic-moves")
    return f


@pytest.fixture
def across_volumes(monkeypatch):
    """The first rename of each source refuses as a cross-volume rename does."""
    real = filer_module._replace
    refused: set[str] = set()

    def replace(src, dst):
        if not str(src).endswith(filer_module.PARTIAL_SUFFIX) and str(src) not in refused:
            refused.add(str(src))
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real(src, dst)

    monkeypatch.setattr(filer_module, "_replace", replace)
    return refused


def _frame(folder, name="frame.fits", payload=b"SIMPLE  =  T" + b"\0" * 2868):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(payload)
    return folder / name


class TestOneVolume:
    def test_it_is_a_rename_never_a_copy(self, filer, tmp_path, monkeypatch):
        monkeypatch.setattr(filer_module.shutil, "copy2", lambda *a, **k: pytest.fail("copied on one volume"))
        src = _frame(tmp_path / "ram" / "n")
        dst = tmp_path / "shared" / "n" / "frame.fits"

        assert filer.move(src, dst) is True
        assert dst.is_file() and not src.exists()


class TestAcrossVolumes:
    def test_the_file_is_never_visible_under_its_name_until_complete(self, filer, tmp_path, monkeypatch, across_volumes):
        src = _frame(tmp_path / "ram" / "n")
        dst = tmp_path / "shared" / "n" / "frame.fits"
        real_copy = shutil.copy2
        seen: list[tuple[bool, bool]] = []

        def copy2(a, b, **k):
            out = real_copy(a, b, **k)
            seen.append(
                (dst.exists(), b.endswith(".frame.fits.partial") if isinstance(b, str) else b.name == ".frame.fits.partial")
            )
            return out

        monkeypatch.setattr(filer_module.shutil, "copy2", copy2)

        assert filer.move(src, dst) is True

        assert seen == [(False, True)], "copied to .frame.fits.partial while frame.fits did not exist"
        assert dst.read_bytes().startswith(b"SIMPLE")
        assert not src.exists()
        assert not (dst.parent / ".frame.fits.partial").exists()

    def test_windows_not_same_device_takes_the_same_path(self, filer, tmp_path, monkeypatch):
        real = filer_module._replace
        first = []

        def replace(a, b):
            if not first:
                first.append(1)
                exc = OSError(None, "The system cannot move the file to a different disk drive")
                exc.winerror = 17  # type: ignore[attr-defined]
                raise exc
            return real(a, b)

        monkeypatch.setattr(filer_module, "_replace", replace)
        src = _frame(tmp_path / "ram" / "n")
        dst = tmp_path / "shared" / "n" / "frame.fits"

        assert filer.move(src, dst) is True
        assert dst.is_file() and not src.exists()

    def test_a_copy_that_fails_leaves_nothing_but_the_source(self, filer, tmp_path, monkeypatch, across_volumes):
        src = _frame(tmp_path / "ram" / "n")
        dst = tmp_path / "shared" / "n" / "frame.fits"

        def copy2(a, b, **k):
            with open(b, "wb") as f:
                f.write(b"SIM")  # half a frame
            raise OSError(errno.ENETRESET, "the share went away mid-copy")

        monkeypatch.setattr(filer_module.shutil, "copy2", copy2)

        assert filer.move(src, dst) is False

        assert src.is_file(), "the source is never touched before the copy is in place"
        assert not dst.exists()
        assert not (dst.parent / ".frame.fits.partial").exists()

    def test_a_partial_left_by_an_earlier_attempt_is_overwritten(self, filer, tmp_path, across_volumes):
        src = _frame(tmp_path / "ram" / "n")
        dst_dir = tmp_path / "shared" / "n"
        dst_dir.mkdir(parents=True)
        (dst_dir / ".frame.fits.partial").write_bytes(b"junk")

        assert filer.move(src, dst_dir / "frame.fits") is True
        assert (dst_dir / "frame.fits").read_bytes().startswith(b"SIMPLE")
        assert not (dst_dir / ".frame.fits.partial").exists()

    def test_another_rename_error_is_not_mistaken_for_cross_volume(self, filer, tmp_path, monkeypatch):
        monkeypatch.setattr(
            filer_module, "_replace", lambda a, b: (_ for _ in ()).throw(PermissionError(errno.EACCES, "denied"))
        )
        monkeypatch.setattr(
            filer_module.shutil, "copy2", lambda *a, **k: pytest.fail("copied after a refusal that was not EXDEV")
        )
        src = _frame(tmp_path / "ram" / "n")

        assert filer.move(src, tmp_path / "shared" / "n" / "frame.fits") is False
        assert src.is_file()

    def test_a_folder_is_moved_file_by_file(self, filer, tmp_path, monkeypatch, across_volumes):
        folder = tmp_path / "ram" / "night" / "seq=0001"
        _frame(folder, "a.fits")
        _frame(folder / "sky", "b.fits")
        dst = tmp_path / "shared" / "night" / "seq=0001"

        assert filer.move(folder, dst) is True

        assert (dst / "a.fits").is_file() and (dst / "sky" / "b.fits").is_file()
        assert not folder.exists()
        assert {os.path.basename(p) for p in across_volumes} == {"a.fits", "b.fits"}


class TestBookkeeping:
    @pytest.mark.parametrize("name", [".frame.fits.partial", ".seq=0001.lock", "seq.txt"])
    def test_is_bookkeeping(self, name):
        assert is_bookkeeping(name)

    @pytest.mark.parametrize("name", ["frame.fits", "frame.partial", "partial", ".hidden"])
    def test_is_not(self, name):
        assert not is_bookkeeping(name)

    def test_a_nested_folders_counter_stays_behind_without_an_error(self, filer, tmp_path, caplog):
        caplog.set_level(logging.ERROR)
        folder = tmp_path / "ram" / "night"
        _frame(folder / "Acquisitions", "a.fits")
        (folder / "Acquisitions" / "seq.txt").write_text("4")
        dst = tmp_path / "shared" / "night"

        filer.move(folder, dst)

        assert (dst / "Acquisitions" / "a.fits").is_file()
        assert (folder / "Acquisitions" / "seq.txt").read_text() == "4", "bookkeeping stays where it is"
        assert not [r for r in caplog.records if "not empty" in r.getMessage()]
