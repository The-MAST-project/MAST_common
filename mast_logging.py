import datetime
import io
import logging
import os
import platform
import time

from rich.logging import RichHandler

from common.filer import Filer

# from common.utils import boxed_lines
# from typing import List

default_log_level = logging.DEBUG


class UtcFormatter(logging.Formatter):
    """
    Formatter whose %(asctime)s is UTC, marked with a trailing 'Z'.

    An observatory's logs are correlated with observations, which are recorded
    in UTC; a local-time stamp that does not say so is worse than useless when
    the two are read side by side. Rollover is UTC as well, so a file's name and
    the lines inside it agree.
    """

    converter = time.gmtime
    default_msec_format = "%s.%03dZ"


class DailyFileHandler(logging.FileHandler):
    """
    A file handler that writes to <base_dir>/<yyyy-mm-dd>/<filename> and follows
    the date, reopening under the new directory when the day turns.

    The date is resolved on every emit rather than baked in at construction, so
    a long-running service keeps rotating instead of writing to its start-up
    day forever.
    """

    def __init__(
        self,
        filename: str,
        base_dir: str | None = None,
        mode="a",
        encoding=None,
        delay=True,
        errors=None,
    ):
        self.leaf = filename
        self.base_dir = base_dir or self.default_base_dir()
        self.current_path: str | None = None
        if "b" not in mode:
            encoding = io.text_encoding(encoding)
        logging.FileHandler.__init__(self, filename="", delay=delay, mode=mode, encoding=encoding, errors=errors)

    @staticmethod
    def default_base_dir() -> str:
        """Used only when no base_dir is supplied; init_log passes Filer's root."""
        if platform.system() == "Windows":
            return os.path.join(os.path.expandvars("%LOCALAPPDATA%"), "mast")
        return "/var/log/mast"

    def make_file_name(self) -> str:
        # UTC, matching the timestamps written inside the file. The day turns at
        # 00:00 UTC everywhere, so units at different sites agree on which file a
        # record belongs to.
        return os.path.join(
            self.base_dir, f"{datetime.datetime.now(datetime.UTC):%Y-%m-%d}", self.leaf
        )

    @property
    def path(self) -> str:
        """The file currently being written to."""
        return self.make_file_name()

    def emit(self, record: logging.LogRecord):
        try:
            self._emit(record)
        except Exception:
            # A full disk or a revoked permission must not take the service down
            # with it; logging's own error path reports it instead.
            self.handleError(record)

    def _emit(self, record: logging.LogRecord):
        filename = self.make_file_name()
        if filename != self.current_path:
            if self.stream is not None:
                self.stream.flush()
                self.stream.close()
                self.stream = None  # type: ignore # See Issue #21742: _open() might fail.
            self.current_path = filename
            self.baseFilename = filename
            os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
            self.stream = self._open()
        logging.StreamHandler.emit(self, record=record)


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


def configure_logging(cli_level: str | None = None) -> int:
    """
    Configure logging for a process.  Call once, from the application entry
    point, BEFORE anything logs.

    Attaches the MAST handlers to the root logger and holds third-party
    libraries at WARNING. Every 'mast.*' logger then inherits both handlers and
    level by propagation, so this is the single place a process decides how
    loudly it talks.

    `cli_level` is whatever a --log-level flag produced (None if absent);
    precedence is CLI > MAST_LOG_LEVEL > default. Returns the level applied.
    """
    level = resolve_log_level(cli_level)
    init_log(level=level)
    quiet_libraries()
    return level


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

    formatter = UtcFormatter(
        "%(asctime)s - %(levelname)-8s - {%(name)s:%(funcName)s:%(threadName)s:%(thread)s} -  %(message)s"
    )
    stream_handlers = [h for h in logger_.handlers if isinstance(h, logging.StreamHandler)]
    if not stream_handlers:
        # handler = logging.StreamHandler()
        # handler.setLevel(level)
        # handler.setFormatter(formatter)
        # logger_.addHandler(handler)

        # show_time=False: rich renders its own timestamp from the local clock,
        # which would contradict the UTC stamp the formatter writes. One clock,
        # one timezone, on both sinks.
        rich_handler = RichHandler(rich_tracebacks=True, show_time=False)
        rich_handler.setLevel(level)
        rich_handler.setFormatter(formatter)
        logger_.addHandler(rich_handler)

    daily_handlers = [h for h in logger_.handlers if isinstance(h, DailyFileHandler)]
    if not daily_handlers:
        handler = DailyFileHandler(
            filename=file_name,
            base_dir=Filer().accessible_shared_root(),
            mode="a",
        )

        handler.setLevel(level)
        handler.setFormatter(formatter)
        logger_.addHandler(handler)
