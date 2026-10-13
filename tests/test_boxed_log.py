"""A box is emitted as one log record, so nothing can be written into the middle of it.

A record per line let another thread's record land between a box's borders. On the
2026-09-08 night that swallowed the longest mount movement into a box.
"""

from __future__ import annotations

import logging

from common.utils import boxed_debug, boxed_lines, boxed_log


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _logger(name: str) -> tuple[logging.Logger, _Capture]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    capture = _Capture()
    logger.handlers = [capture]
    return logger, capture


def test_a_box_is_one_record_holding_every_line():
    logger, capture = _logger("test.boxed_log.one_record")
    lines = ["acquisition completed", "telescope is tracking", "moving stage to SPEC"]

    boxed_log(logger, lines)

    assert len(capture.records) == 1
    assert capture.records[0].getMessage().split("\n") == boxed_lines(lines)


def test_the_level_is_kept():
    logger, capture = _logger("test.boxed_log.level")

    boxed_debug(logger, "one line")

    assert [r.levelno for r in capture.records] == [logging.DEBUG]
