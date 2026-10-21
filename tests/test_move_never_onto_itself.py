"""A move onto itself, or into its own subtree, is refused -- it used to delete the files.

With neither D: nor Z: mapped, ``ram``, ``shared`` and ``local`` are all ``C:/MAST/``. A
ram -> shared move is then a move onto itself, and ``_merge_into`` compared every file with
itself, found it "identical" and unlinked it: on mast00, 2026-10-13, ``Filer().move(p, p)``
deleted ``frame.fits`` and kept only ``seq.txt``. In that same state the start-up relocation
sweep took every folder under ``C:/MAST/`` for a ram leftover. Item 0 of
plans/filing-areas-design.md (1.2, 1.5).
"""

from __future__ import annotations

import platform

import pytest

from common.filer import Filer, Location, MoveGuardian


@pytest.fixture
def make_filer(tmp_path, monkeypatch):
    """A Filer whose ram / shared / local are the given folders under tmp_path -- collapsed
    onto one when given the same name, as on a unit with nothing mapped."""
    monkeypatch.setattr(Filer, "_mover_pool", None)
    monkeypatch.setattr(Filer, "_ensure_sweeper", lambda self: None)
    with Filer._pending_lock:
        Filer._pending.clear()
        Filer._in_flight.clear()
    with MoveGuardian._condition:
        MoveGuardian._protected.clear()
        MoveGuardian._moving.clear()
        MoveGuardian._products.clear()

    def make(ram: str = "ram", shared: str = "shared", local: str = "local") -> Filer:
        roots = {name: tmp_path / name for name in {ram, shared, local}}
        for path in roots.values():
            path.mkdir(exist_ok=True)

        def fake_init(self, logger=None):
            # Posix-style, as production roots are on every platform ("D:/" + "MAST/").
            self.ram = Location(f"{roots[ram].as_posix()}/")
            self.shared = Location(f"{roots[shared].as_posix()}/")
            self.local = Location(f"{roots[local].as_posix()}/")
            self.share_root = self.shared
            self.machine = self.shared
            self.logger = logger

        monkeypatch.setattr(Filer, "__init__", fake_init)
        return Filer()

    return make


def _product_folder(root):
    folder = root / "2026-10-13" / "Acquisitions" / "seq=0001"
    folder.mkdir(parents=True)
    (folder / "frame.fits").write_bytes(b"SIMPLE  =  T" + b"\0" * 64)
    (folder / "seq.txt").write_text("1")
    return folder


class TestMove:
    def test_a_folder_onto_itself_is_refused_and_keeps_its_files(self, make_filer, tmp_path):
        filer = make_filer()
        folder = _product_folder(tmp_path / "ram")

        assert filer.move(folder, folder) is True  # nothing a retry could change
        assert (folder / "frame.fits").is_file()
        assert (folder / "seq.txt").is_file()

    def test_a_file_onto_itself_is_refused(self, make_filer, tmp_path):
        filer = make_filer()
        frame = _product_folder(tmp_path / "ram") / "frame.fits"

        assert filer.move(frame, frame) is True
        assert frame.is_file()

    def test_into_its_own_subtree_is_refused(self, make_filer, tmp_path):
        filer = make_filer()
        folder = _product_folder(tmp_path / "ram")

        assert filer.move(folder, folder / "inside") is True
        assert (folder / "frame.fits").is_file()
        assert not (folder / "inside").exists()

    def test_out_onto_its_own_parent_is_refused(self, make_filer, tmp_path):
        filer = make_filer()
        folder = _product_folder(tmp_path / "ram")

        assert filer.move(folder, folder.parent) is True
        assert (folder / "frame.fits").is_file()

    def test_the_same_path_spelled_differently_is_refused(self, make_filer, tmp_path):
        filer = make_filer()
        folder = _product_folder(tmp_path / "ram")

        assert filer.move(folder, folder / "x" / "..") is True
        assert (folder / "frame.fits").is_file()

    @pytest.mark.skipif(platform.system() != "Windows", reason="case-insensitive file system")
    def test_a_case_variant_is_the_same_path_on_windows(self, make_filer, tmp_path):
        filer = make_filer()
        folder = _product_folder(tmp_path / "ram")

        assert filer.move(folder, str(folder).upper()) is True
        assert (folder / "frame.fits").is_file()

    def test_a_real_move_still_moves(self, make_filer, tmp_path):
        filer = make_filer()
        folder = _product_folder(tmp_path / "ram")
        dst = tmp_path / "shared" / "2026-10-13" / "Acquisitions" / "seq=0001"

        assert filer.move(folder, dst) is True
        assert (dst / "frame.fits").is_file()
        assert not (folder / "frame.fits").exists()


class TestCollapsedRoots:
    def test_move_ram_to_shared_onto_the_same_root_keeps_the_frame(self, make_filer, tmp_path):
        """The mast00 case end to end: ram and shared are one folder."""
        filer = make_filer(ram="mast", shared="mast", local="mast")
        folder = _product_folder(tmp_path / "mast")

        filer.move_ram_to_shared(str(folder))
        assert filer.flush(timeout=10)

        assert (folder / "frame.fits").is_file()

    def test_the_sweep_does_not_run_when_ram_is_the_local_disk(self, make_filer, tmp_path):
        filer = make_filer(ram="mast", shared="shared", local="mast")
        folder = _product_folder(tmp_path / "mast")

        assert filer.start_product_relocation_sweep() is False
        assert (folder / "frame.fits").is_file()

    def test_the_sweep_does_not_run_when_ram_is_the_share(self, make_filer, tmp_path):
        filer = make_filer(ram="mast", shared="mast", local="local")
        _product_folder(tmp_path / "mast")

        assert filer.start_product_relocation_sweep() is False
