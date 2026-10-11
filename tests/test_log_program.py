"""Each program on a machine gets its own daily file, named from the configured role (#148).

The caller names its program and never the file, so the machine's identity stays in
config.toml. A second call for another program must not be swallowed: the existing
handler would keep writing, and two processes would share one file.
"""

import logging
import textwrap
from collections.abc import Iterator

import pytest

from common.config.local import load_local_config
from common.mast_logging import DailyFileHandler, LogProgram, configure_logging, init_log

VALID_TOML = textwrap.dedent(
    """
    site = "ns"
    project = "mast"
    machine_role = "{role}"
    controller_host = "mast-ns-control"
    database = "mast"
    domain = "weizmann.ac.il"

    [location]
    latitude = 30.04
    longitude = 35.02
    elevation = 400
    """
)


@pytest.fixture
def role(tmp_path, monkeypatch) -> Iterator[str]:
    cfg = tmp_path / "config.toml"
    cfg.write_text(VALID_TOML.format(role="spec"))
    monkeypatch.setenv("MAST_CONFIG", str(cfg))
    load_local_config.cache_clear()
    yield "spec"
    load_local_config.cache_clear()


class _Filer:
    def __init__(self, root: str) -> None:
        self._root = root

    def machine_log_root(self) -> str:
        return self._root


@pytest.fixture
def logger(tmp_path, monkeypatch) -> Iterator[logging.Logger]:
    monkeypatch.setattr("common.mast_logging.Filer", lambda: _Filer(str(tmp_path)))
    lg = logging.getLogger("mast.test.log_program")
    yield lg
    for handler in list(lg.handlers):
        lg.removeHandler(handler)
        handler.close()


def daily_leaves(lg: logging.Logger) -> list[str]:
    return [h.leaf for h in lg.handlers if isinstance(h, DailyFileHandler)]


@pytest.mark.parametrize(
    ("program", "expected"),
    [(LogProgram.APP, "mast-spec-log.txt"), (LogProgram.SUPERVISOR, "mast-spec-supervisor-log.txt")],
)
def test_the_file_is_named_from_the_role_and_the_program(role, logger, program, expected):
    init_log(logger, program=program)

    assert daily_leaves(logger) == [expected]


def test_the_default_program_keeps_the_existing_name(role, logger):
    init_log(logger)

    assert daily_leaves(logger) == [f"mast-{role}-log.txt"]


def test_an_unreadable_config_falls_back_to_startup_for_any_program(monkeypatch, logger):
    monkeypatch.setenv("MAST_CONFIG", "/nonexistent/config.toml")
    load_local_config.cache_clear()

    init_log(logger, program=LogProgram.SUPERVISOR)

    assert daily_leaves(logger) == ["mast-STARTUP-supervisor-log.txt"]


def test_a_second_call_for_the_same_program_adds_nothing(role, logger):
    init_log(logger, program=LogProgram.SUPERVISOR)
    init_log(logger, program=LogProgram.SUPERVISOR)

    assert daily_leaves(logger) == ["mast-spec-supervisor-log.txt"]


def test_a_second_call_for_another_program_raises(role, logger):
    init_log(logger)

    with pytest.raises(ValueError, match="mast-spec-log.txt"):
        init_log(logger, program=LogProgram.SUPERVISOR)


def test_configure_logging_passes_the_program_through(role, monkeypatch):
    calls = []
    monkeypatch.setattr("common.mast_logging.init_log", lambda **kwargs: calls.append(kwargs))

    configure_logging(program=LogProgram.SUPERVISOR)

    assert calls[0]["program"] is LogProgram.SUPERVISOR
