import datetime
import io
import logging
import os
import platform

from rich.logging import RichHandler

from common.filer import Filer
from common.paths import PathMaker

# from common.utils import boxed_lines
# from typing import List

default_log_level = logging.DEBUG


class DailyFileHandler(logging.FileHandler):
    filename: str = ""
    path: str

    def make_file_name(self):
        """
        Produces file names for the DailyFileHandler, which rotates them daily at noon (UT).
        The filename has the format <top><daily><bottom> and includes:
        * A top section (either /var/log/mast on Linux or %LOCALAPPDATA%/mast on Windows
        * The daily section (current date as %Y-%m-%d)
        * The bottom path, supplied by the user
        Examples:
        * /var/log/mast/2022-02-17/server/app.log
        * c:\\User\\User\\LocalAppData\\mast\\2022-02-17\\main.log
        :return:
        """
        top = ""
        if platform.platform() == "Linux":
            top = "/var/log/mast"
        elif platform.platform().startswith("Windows"):
            top = os.path.join(os.path.expandvars("%LOCALAPPDATA%"), "mast")
        now = datetime.datetime.now()
        if now.hour < 12:
            now = now - datetime.timedelta(days=1)
        return os.path.join(top, f"{now:%Y-%m-%d}", self.path)

    def emit(self, record: logging.LogRecord):
        """
        Overrides the logging.FileHandler's emit method.  It is called every time a log record is to be emitted.
        This function checks whether the handler's filename includes the current date segment.
        If not:
        * A new file name is produced
        * The handler's stream is closed
        * A new stream is opened for the new file
        The record is emitted.
        :param record:
        :return:
        """
        filename = self.make_file_name()
        if filename != self.filename:
            if self.stream is not None:
                # we have an open file handle, clean it up
                self.stream.flush()
                self.stream.close()
                self.stream = None  # type: ignore # See Issue #21742: _open () might fail.

            self.baseFilename = filename
            os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
            self.stream = self._open()
        logging.StreamHandler.emit(self, record=record)

    def __init__(self, path: str, mode="a", encoding=None, delay=True, errors=None):
        self.path = path
        if "b" not in mode:
            encoding = io.text_encoding(encoding)
        logging.FileHandler.__init__(self, filename="", delay=delay, mode=mode, encoding=encoding, errors=errors)


def get_logger(name: str) -> logging.Logger:
    """
    The one way MAST code should obtain a logger:  logger = get_logger(__name__)

    Everything lands under the 'mast.' prefix so the whole application can be
    levelled as a single subtree (logging.getLogger("mast").setLevel(...)) while
    third-party libraries stay independently controllable.

    The prefix has to be added here rather than relying on __name__ alone: each
    app directory is its own import root, so __name__ is 'common.utils' in the
    shared library but a bare 'guiding' or 'highspec' in the consumers. Only
    'common.*' would form a hierarchy on its own; the rest would sit directly
    under root alongside httpx and pymongo.
    """
    return logging.getLogger(name if name == "mast" or name.startswith("mast.") else f"mast.{name}")


# Libraries that log copiously at INFO/DEBUG and drown out application output
# once handlers live on the root logger.
NOISY_LIBRARIES = (
    "httpx",
    "httpcore",
    "pymongo",
    "matplotlib",
    "asyncio",
    "watchdog",
    "urllib3",
    "websockets",
    "PIL",
)


def quiet_libraries(level: int = logging.WARNING) -> None:
    """
    Hold third-party loggers at `level`.

    Needed only because handlers now live on the root logger: before that, every
    MAST logger carried its own handlers and library records had nowhere to go.
    Call once, right after init_log().
    """
    for name in NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(level)


def resolve_log_level(cli_level: str | None = None) -> int:
    """
    Resolve the effective log level.  Precedence: CLI > MAST_LOG_LEVEL > default.

    Accepts a level name ('DEBUG', case-insensitive) or a number. Raises on an
    unrecognised value rather than silently falling back, so a typo in a service
    definition fails loudly at startup instead of quietly changing verbosity.
    """
    for value in (cli_level, os.getenv("MAST_LOG_LEVEL")):
        if not value:
            continue
        text = str(value).strip().upper()
        if text.isdigit():
            return int(text)
        level = logging.getLevelName(text)
        if isinstance(level, int):
            return level
        raise ValueError(f"invalid log level: {value!r} (expected a name such as DEBUG, or a number)")
    return default_log_level


def init_log(
    logger_: logging.Logger | None = None,
    level: int | None = None,
):
    """
    Attach the MAST handlers (rich console + daily file).

    Call this ONCE per process, on the root logger, from the application entry
    point:  init_log()  --  every 'mast.*' logger then inherits both the level
    and the handlers by propagation.

    Passing an explicit logger is the pre-centralisation form and is retained
    only so consumers that still call it per module keep working until they are
    converted. That path also keeps propagate=False, as it always did, so a
    logger with its own handlers cannot emit twice.
    """
    logger_ = logging.getLogger() if logger_ is None else logger_
    if logger_ is not logging.getLogger():
        # Legacy per-logger form; see the docstring.
        logger_.propagate = False
    level = resolve_log_level() if level is None else level
    logger_.setLevel(level)
    role = os.getenv("MAST_PROJECT", "unknown_role")
    file_name = f"mast-{role}-log.txt"

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)-8s - {%(name)s:%(funcName)s:%(threadName)s:%(thread)s} -  %(message)s"
    )
    stream_handlers = [h for h in logger_.handlers if isinstance(h, logging.StreamHandler)]
    if not stream_handlers:
        # handler = logging.StreamHandler()
        # handler.setLevel(level)
        # handler.setFormatter(formatter)
        # logger_.addHandler(handler)

        rich_handler = RichHandler(rich_tracebacks=True)
        rich_handler.setLevel(level)
        logger_.addHandler(rich_handler)

    daily_handlers = [h for h in logger_.handlers if isinstance(h, DailyFileHandler)]
    if not daily_handlers:
        root = Filer().accessible_shared_root()
        handler = DailyFileHandler(
            path=os.path.join(
                PathMaker().make_daily_folder_name(root=root),
                file_name,
            ),
            mode="a",
        )

        handler.setLevel(level)
        handler.setFormatter(formatter)
        logger_.addHandler(handler)
