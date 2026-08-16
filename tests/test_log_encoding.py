"""The daily log file must hold non-ASCII.

`io.text_encoding(None)` resolves to the locale encoding, which is cp1252 on the
Windows machines this runs on. A single U+2033 in a log message was then dropped
from the file and reported as a UnicodeEncodeError traceback on stderr, while the
console handler -- which has no such limit -- showed the message normally. The two
sinks disagreeing about what happened is the part that matters: the file is what
gets read the morning after.
"""

import logging

from common.mast_logging import DailyFileHandler

# Degree sign, double prime, arrow, en dash: the characters this codebase actually
# reaches for when it writes about angles and mappings.
NON_ASCII = "45.5° offset, 1.5″ step → done – ok"


def _emit_through_handler(base_dir, message: str) -> DailyFileHandler:
    handler = DailyFileHandler(filename="test.log", base_dir=str(base_dir))
    handler.emit(
        logging.LogRecord(name="test", level=logging.INFO, pathname=__file__, lineno=1, msg=message, args=(), exc_info=None)
    )
    handler.close()
    return handler


def test_non_ascii_record_reaches_the_file(tmp_path):
    handler = _emit_through_handler(tmp_path, NON_ASCII)

    with open(handler.path, encoding="utf-8") as fp:
        assert NON_ASCII in fp.read()


def test_handler_defaults_to_utf8(tmp_path):
    """Pinned explicitly: the test above would also pass on a UTF-8 locale, which is
    every CI runner, so it alone would not have caught the original defect."""
    handler = _emit_through_handler(tmp_path, "ascii only")

    assert handler.encoding.lower().replace("-", "") == "utf8"


def test_explicit_encoding_is_respected(tmp_path):
    """The default is a default, not an override -- a caller asking for something
    else still gets it."""
    handler = DailyFileHandler(filename="test.log", base_dir=str(tmp_path), encoding="latin-1")

    assert handler.encoding == "latin-1"
