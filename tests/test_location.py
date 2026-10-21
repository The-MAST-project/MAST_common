"""`Location`: one canonical root, and live readable / writable checks.

plans/filing-areas-design.md 4.1-4.2, item 2. Canonicalisation is string work, so the
Windows and UNC spellings are tested on every runner. The probes run against temporary
folders; a hung share is simulated with a probe that blocks, never waited for for real.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

import common.filer as filer_module
from common.filer import Location, canonical_root


@pytest.fixture(autouse=True)
def fresh_probes(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})


class TestCanonicalRoot:
    @pytest.mark.parametrize(
        "given, canonical",
        [
            ("D:/MAST/", "D:/MAST/"),
            ("D:\\MAST\\", "D:/MAST/"),
            ("D:\\MAST", "D:/MAST/"),
            ("D:/MAST", "D:/MAST/"),
            ("D://MAST//mast00//", "D:/MAST/mast00/"),
            ("D:", "D:/"),
            ("C:\\", "C:/"),
            ("/Storage/mast-share/MAST", "/Storage/mast-share/MAST/"),
            ("/Storage//mast-share/MAST//", "/Storage/mast-share/MAST/"),
            ("/", "/"),
        ],
    )
    def test_forward_slashed_one_trailing_slash(self, given, canonical):
        assert canonical_root(given) == canonical

    @pytest.mark.parametrize(
        "given",
        [
            "\\\\mast-ns-control\\mast-share\\MAST\\mast00",
            "//mast-ns-control/mast-share/MAST/mast00",
            "//mast-ns-control//mast-share/MAST/mast00/",
            "\\\\mast-ns-control/mast-share\\MAST/mast00\\",
        ],
    )
    def test_a_unc_root_keeps_its_leading_double_slash(self, given):
        assert canonical_root(given) == "//mast-ns-control/mast-share/MAST/mast00/"

    @pytest.mark.parametrize("given", ["", "//", "\\\\"])
    def test_nothing_to_root_is_refused(self, given):
        with pytest.raises(ValueError):
            canonical_root(given)

    def test_the_root_is_canonical_on_construction(self):
        assert Location("C:\\MAST").root == "C:/MAST/"

    def test_the_old_two_part_form_joins_to_the_same_root(self):
        assert Location("C:/", "MAST/") == Location("C:/MAST/")
        assert Location(None, "/Storage/mast-share/MAST").root == "/Storage/mast-share/MAST/"

    def test_an_empty_root_is_refused(self):
        with pytest.raises(ValueError):
            Location("")


class TestComparison:
    def test_case_insensitive_where_the_file_system_is(self, monkeypatch):
        monkeypatch.setattr(filer_module, "_CASE_INSENSITIVE", True)
        assert Location("D:/MAST/") == Location("d:\\mast")
        assert hash(Location("D:/MAST/")) == hash(Location("d:/mast/"))

    def test_case_sensitive_where_it_is_not(self, monkeypatch):
        monkeypatch.setattr(filer_module, "_CASE_INSENSITIVE", False)
        assert Location("/Storage/MAST/") != Location("/storage/mast/")

    def test_separators_do_not_matter(self):
        assert Location("D:\\MAST\\") == Location("D:/MAST")


class TestReadable:
    def test_an_existing_folder(self, tmp_path):
        assert Location(str(tmp_path)).readable()

    def test_a_missing_folder(self, tmp_path):
        assert not Location(str(tmp_path / "absent")).readable()


class TestWritable:
    def test_an_existing_folder_and_it_leaves_nothing_behind(self, tmp_path):
        assert Location(str(tmp_path)).writable()
        assert os.listdir(tmp_path) == []

    def test_a_missing_root_is_not_writable_and_is_not_created(self, tmp_path):
        missing = tmp_path / "absent"
        assert not Location(str(missing)).writable()
        assert not missing.exists()

    def test_concurrent_probes_do_not_collide(self, tmp_path, monkeypatch):
        monkeypatch.setattr(filer_module, "PROBE_CACHE_SECONDS", 0.0)
        location = Location(str(tmp_path))
        answers: list[bool] = []
        threads = [threading.Thread(target=lambda: answers.append(location.writable())) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert answers == [True] * 8
        assert os.listdir(tmp_path) == []


class TestCache:
    def test_an_answer_is_reused_within_the_window(self, tmp_path, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(filer_module, "_probe_clock", lambda: now[0])
        folder = tmp_path / "comes-and-goes"
        folder.mkdir()
        location = Location(str(folder))

        assert location.readable()
        folder.rmdir()
        now[0] += filer_module.PROBE_CACHE_SECONDS / 2
        assert location.readable(), "within the window the cached answer stands"

        now[0] += filer_module.PROBE_CACHE_SECONDS
        assert not location.readable(), "after it, the root is probed again"

    def test_the_cache_is_shared_across_instances(self, tmp_path, monkeypatch):
        # Filer() builds new Locations each time; a per-instance cache would never hit.
        monkeypatch.setattr(filer_module, "_probe_clock", lambda: 1000.0)
        folder = tmp_path / "x"
        folder.mkdir()
        assert Location(str(folder)).readable()
        folder.rmdir()
        assert Location(str(folder) + "/").readable()


class TestAHungProbe:
    def test_times_out_and_is_not_started_again_while_it_hangs(self, monkeypatch):
        monkeypatch.setattr(filer_module, "PROBE_CACHE_SECONDS", 0.0)
        release = threading.Event()
        started: list[int] = []

        def hang(path):
            started.append(1)
            release.wait(5)
            return True

        monkeypatch.setattr(filer_module.os.path, "isdir", hang)
        location = Location("//mast-ns-control/mast-share/MAST/")

        began = time.monotonic()
        assert not location.readable(timeout=0.05)
        assert time.monotonic() - began < 1.0
        assert not location.readable(timeout=0.05), "still hung: refused without a new probe"
        assert started == [1]

        release.set()
        filer_module._probe_threads[next(iter(filer_module._probe_threads))][0].join(1)
        assert location.readable(timeout=1.0), "once it returns, the next probe runs"
        assert started == [1, 1]
