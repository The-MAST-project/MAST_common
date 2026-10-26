"""The daily log file follows the SHARE filing area (plans/filing-areas-design.md 10).

It used to take its directory once, at start-up (`machine_log_root()`): a process started
with the share down logged to `C:` until it exited, and one whose share dropped later lost
file logging. Now it asks for the share's current root on every record, writes
`*-log.fallback.txt` while the share is demoted, demotes it itself on a failed open or
write, and stops -- console only -- while the local disk is at its ceiling.
"""

from __future__ import annotations

import datetime
import logging
import os

import pytest

import common.filer as filer_module
from common.filer import Filer, FilingArea, MoveGuardian
from common.mast_logging import DailyFileHandler, fallback_log_name, observing_night
from common.testing.filer import DiskUsage, reset_filing_areas


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(filer_module, "_probe_cache", {})
    monkeypatch.setattr(filer_module, "_probe_threads", {})
    monkeypatch.setattr(Filer, "MONITOR_INTERVAL_SECONDS", 3600.0)
    reset_filing_areas()
    yield
    reset_filing_areas()


@pytest.fixture
def handler(request):
    h = DailyFileHandler(filename=f"mast-{request.node.name}-log.txt", follow_share=True)
    h.setFormatter(logging.Formatter("%(message)s"))
    yield h
    h.close()


def _night() -> str:
    return observing_night(datetime.datetime.now(datetime.UTC))


def _emit(h: DailyFileHandler, message: str) -> None:
    h.emit(logging.LogRecord("t", logging.INFO, __file__, 1, message, None, None))


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _net_error() -> OSError:
    exc = OSError(None, "The network path was not found")
    exc.winerror = 53  # type: ignore[attr-defined]
    return exc


def _on_share(h) -> str:
    return os.path.join(Filer().shared.root, _night(), h.leaf)


def _on_fallback(h) -> str:
    return os.path.join(Filer().share_fallback.root, _night(), fallback_log_name(h.leaf))


class TestTheFile:
    def test_on_this_machines_folder_on_the_share(self, handler):
        _emit(handler, "hello")
        assert _read(_on_share(handler)) == "hello\n"

    def test_on_the_fallback_under_its_own_name_while_the_share_is_demoted(self, handler):
        _emit(handler, "before")
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())
        _emit(handler, "during")

        assert _read(_on_share(handler)) == "before\n"
        assert _read(_on_fallback(handler)) == "during\n"

    def test_back_on_the_share_once_promoted(self, handler):
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())
        _emit(handler, "during")
        assert Filer().check_now(FilingArea.SHARE)
        _emit(handler, "after")

        assert _read(_on_share(handler)) == "after\n"

    def test_nowhere_while_the_local_disk_is_at_its_ceiling(self, handler, monkeypatch):
        monkeypatch.setattr(filer_module, "_disk_usage", lambda path: DiskUsage(total=100, used=95, free=5))
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())

        _emit(handler, "dropped from the file")

        assert handler.path is None
        assert not os.path.exists(_on_fallback(handler))


class TestFailures:
    def test_a_failed_open_on_the_share_demotes_it_and_the_record_lands_on_the_fallback(self, handler, monkeypatch):
        real_open = DailyFileHandler._open
        share = Filer().shared.root

        def _open(self):
            if self.baseFilename.replace("\\", "/").startswith(share):
                raise _net_error()
            return real_open(self)

        monkeypatch.setattr(DailyFileHandler, "_open", _open)

        _emit(handler, "first")

        assert Filer().is_demoted(FilingArea.SHARE)
        assert _read(_on_fallback(handler)) == "first\n"

    def test_a_failed_write_demotes_it_and_is_retried_on_the_fallback(self, handler):
        _emit(handler, "one")

        class Broken:
            def write(self, s):
                raise _net_error()

            def flush(self):
                pass

            def close(self):
                pass

        handler.stream = Broken()
        _emit(handler, "two")

        assert Filer().is_demoted(FilingArea.SHARE)
        assert _read(_on_fallback(handler)) == "two\n"

    def test_a_path_level_failure_does_not_move_the_log(self, handler, monkeypatch):
        def _open(self):
            raise PermissionError(13, "denied")

        monkeypatch.setattr(DailyFileHandler, "_open", _open)

        _emit(handler, "lost to the file, kept on the console")

        assert not Filer().is_demoted(FilingArea.SHARE)


class TestReentrancy:
    def test_a_record_from_inside_an_emit_goes_to_stderr_then_to_the_file(self, handler, monkeypatch, capsys):
        real = Filer.report_failure

        def report_failure(self, area, path, exc):
            # As a demotion logged through the same handler would do.
            _emit(handler, "logged from inside")
            return real(self, area, path, exc)

        monkeypatch.setattr(Filer, "report_failure", report_failure)
        _emit(handler, "one")

        class Broken:
            def write(self, s):
                raise _net_error()

            def flush(self):
                pass

            def close(self):
                pass

        handler.stream = Broken()
        _emit(handler, "two")

        assert "logged from inside" in capsys.readouterr().err
        assert _read(_on_fallback(handler)) == "logged from inside\ntwo\n"


class TestTheName:
    @pytest.mark.parametrize(
        "leaf, fallback",
        [
            ("mast-unit-log.txt", "mast-unit-log.fallback.txt"),
            ("mast-unit-supervisor-log.txt", "mast-unit-supervisor-log.fallback.txt"),
            ("mast-STARTUP-log.txt", "mast-STARTUP-log.fallback.txt"),
            ("noextension", "noextension.fallback"),
        ],
    )
    def test_fallback_log_name(self, leaf, fallback):
        assert fallback_log_name(leaf) == fallback

    def test_a_fixed_base_dir_does_not_follow(self, tmp_path):
        h = DailyFileHandler(filename="fixed.log", base_dir=str(tmp_path))
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())
        assert h.path == (tmp_path / _night() / "fixed.log").as_posix()

    def test_forward_slashed_like_the_roots_it_is_built_on(self, handler):
        # os.path.join used to put a backslash after the forward-slashed root:
        # '//mast-ns-control/mast-share/MAST/mast00/2026-10-24\mast-unit-log.txt'.
        assert "\\" not in handler.path
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())
        assert "\\" not in handler.path


class TestTheDrain:
    """The fallback's drain leaves the night folder the handler writes in alone (a claim), and
    takes it once the handler has moved on. It used to remove that folder, empty because the
    handler had only just made it, from under the handler: the record was lost, on the Windows
    CI runner, three times."""

    def _promote_without_draining(self):
        # check_now would also start a drain, on its own thread: these tests drain by hand.
        with Filer._state_lock:
            Filer._demoted.pop(FilingArea.SHARE, None)

    def test_a_drain_leaves_the_folder_being_written_alone(self, handler):
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())
        _emit(handler, "on the fallback")
        self._promote_without_draining()

        Filer().drain_fallbacks()

        assert _read(_on_fallback(handler)) == "on the fallback\n", "claimed: not moved, not removed"

    def test_an_empty_folder_just_made_survives_a_drain(self, handler, tmp_path):
        night = os.path.join(Filer().share_fallback.root, "2026-10-26")
        handler._claim_folder_for(night)
        os.makedirs(night, exist_ok=True)
        self._promote_without_draining()

        Filer().drain_fallbacks()

        assert os.path.isdir(night), "claimed before it was made: the drain's empty-folder sweep left it"
        handler._release_folder()

    def test_once_the_handler_moves_on_the_next_drain_takes_it(self, handler):
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())
        _emit(handler, "during")
        fallback_file = _on_fallback(handler)
        self._promote_without_draining()
        _emit(handler, "after")  # back on the share: the fallback folder is released

        Filer().drain_fallbacks()

        assert not os.path.exists(fallback_file)
        moved = os.path.join(Filer().shared.root, _night(), fallback_log_name(handler.leaf))
        assert _read(moved) == "during\n"

    def test_closing_releases_it(self, handler):
        Filer().report_failure(FilingArea.SHARE, "x", _net_error())
        _emit(handler, "x")
        night = os.path.dirname(_on_fallback(handler))
        handler.close()
        assert not MoveGuardian().folder_is_claimed(night)
