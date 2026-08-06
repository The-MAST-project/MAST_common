import datetime
import io
import logging
import os
import platform
import time

from rich.logging import RichHandler
from rich.text import Text

from common.filer import Filer

default_log_level = logging.DEBUG


class UtcFormatter(logging.Formatter):
    """
    Formatter whose %(asctime)s is UTC, marked with a trailing 'Z'.

    An observatory's logs are correlated with observations, which are recorded
    in UTC; a local-time stamp that does not say so is worse than useless when
    the two are read side by side.

    Note that a record's own stamp is a true UTC instant, while the directory it
    lands in is an observing-night label (see observing_night) -- past 00:00 UTC
    the two names differ by a day, by design.
    """

    converter = time.gmtime
    default_msec_format = "%s.%03dZ"


def observing_night(when: datetime.datetime) -> str:
    """
    The observing-night label for an instant: the UTC date twelve hours earlier.

    A night spans local midnight, so a calendar date splits it in two. At this
    site UTC midnight falls at 02:00-03:00 local -- the middle of the run -- so
    dating by the calendar would break every night across two directories.
    Anchoring at 12:00 UTC instead gives a night one label, that of the evening
    it began, and rolls over at 14:00-15:00 local, in daylight.

    This is the Julian Date's noon epoch, and the same convention Config uses to
    build the night window (SiteConfig.night_window anchors on 12:00 UTC and
    takes the next sunset), so the scheduler and the logs agree on which night a
    record belongs to. `when` must be timezone-aware.
    """
    return f"{when - datetime.timedelta(hours=12):%Y-%m-%d}"


def utc_log_time(when: datetime.datetime) -> Text:
    """
    Rich's console timestamp: UTC time-of-day, milliseconds and a 'Z'.

    Deliberately shorter than UtcFormatter's, which keeps the date for the file:
    the console is read live, where every line carries the same date, and the
    daily log already states it in both the directory name and each record.
    """
    return Text(f"{when:%H:%M:%S}.{when.microsecond // 1000:03d}Z")


class UtcRichHandler(RichHandler):
    """
    RichHandler that renders its time column in UTC.

    The timestamp belongs in Rich's own column rather than in the format string.
    The column is drawn with a single 'log.time' style, whereas anything inside the
    message is passed through ReprHighlighter, which colours each date group as a
    number and reads '15:51:51' as an IPv6 address -- one timestamp in three
    colours. The highlighter stays on for the message body, where it is useful.

    Rich builds the column from `datetime.fromtimestamp(record.created)`, i.e. local
    time, which would contradict the UTC stamp going to the file; one clock, one
    timezone, on both sinks. render() is therefore Rich's own, with that single
    conversion made UTC-aware.
    """

    def render(self, *, record: logging.LogRecord, traceback, message_renderable):
        return self._log_render(
            self.console,
            [message_renderable] if not traceback else [message_renderable, traceback],
            log_time=datetime.datetime.fromtimestamp(record.created, datetime.UTC),
            time_format=None if self.formatter is None else self.formatter.datefmt,
            level=self.get_level_text(record),
            path=os.path.basename(record.pathname),
            line_no=record.lineno,
            link_path=record.pathname if self.enable_link_path else None,
        )


class DailyFileHandler(logging.FileHandler):
    """
    A file handler that writes to <base_dir>/<yyyy-mm-dd>/<filename> and follows
    the observing night, reopening under the new directory when the night turns.

    <yyyy-mm-dd> is an observing-night label (see observing_night), not a
    calendar date: the directory turns at 12:00 UTC, so a night's records stay
    in one place instead of splitting at local 02:00.

    The night is resolved on every emit rather than baked in at construction, so
    a long-running service keeps rotating instead of writing to its start-up
    night forever. Rotation is lazy in the sense that it happens on the first
    record after the turn: an idle service creates no directory until it has
    something to say.
    """

    # After a failed open the share is not probed again for this long. Retrying on
    # every record would block the whole service inside SMB timeouts once the
    # shared drive hangs -- the console handler still carries those records.
    REOPEN_RETRY_INTERVAL_SEC = 30.0

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
        self._next_open_attempt: float = 0.0
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
        # The directory is an observing night, not a calendar day, so a night's
        # records stay together (see observing_night). It turns at 12:00 UTC
        # everywhere, so units at different sites still agree on which file a
        # record belongs to.
        return os.path.join(self.base_dir, observing_night(datetime.datetime.now(datetime.UTC)), self.leaf)

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
        # `self.stream is None` is not only the delay=True first record: an earlier
        # open may have failed (unreachable share), or close() may have dropped the
        # stream. Without this test such a handler would take the "nothing changed"
        # path forever and write to None on every record.
        if self.stream is None or filename != self.current_path:
            if not self._reopen(filename):
                return  # share still unreachable; the console handler keeps the record
        logging.StreamHandler.emit(self, record=record)

    def _reopen(self, filename: str) -> bool:
        """
        Point the handler at `filename`, returning False when the attempt is being
        held off after a recent failure.

        State is committed only once the file is actually open: recording the new
        path first would leave a failed open looking like a successful one, and
        every later record would then take the fast path onto a None stream.
        """
        if self.stream is not None:
            try:
                self.stream.flush()
                self.stream.close()
            except OSError:
                pass  # the file we are leaving behind; nothing useful to do
            finally:
                self.stream = None  # type: ignore # See Issue #21742: _open() might fail.

        now = time.monotonic()
        if now < self._next_open_attempt:
            return False
        try:
            self.baseFilename = filename
            os.makedirs(os.path.dirname(filename), exist_ok=True)
            self.stream = self._open()
        except OSError:
            self.stream = None  # type: ignore
            self._next_open_attempt = now + self.REOPEN_RETRY_INTERVAL_SEC
            raise  # emit() reports it through handleError, at most once per interval
        self.current_path = filename
        self._next_open_attempt = 0.0
        return True


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
    # The machine role comes from the bootstrap config file (single source of
    # truth). init_log() runs at import time, so this must not force an eager
    # config load or raise: guard it and fall back to a STARTUP marker. Once the
    # config is loadable (load_local_config is lru_cached), later init_log calls
    # resolve the real role. mast-STARTUP-log.txt therefore means "logged before
    # the config could be read" — not an error value.
    try:
        from common.config.local import load_local_config

        role = load_local_config().machine_role
    except Exception:
        role = "STARTUP"
    file_name = f"mast-{role}-log.txt"

    formatter = UtcFormatter(
        "%(asctime)s - %(levelname)-8s - {%(name)s:%(funcName)s:%(threadName)s:%(thread)s} -  %(message)s"
    )
    # The console carries the message and nothing else. RichHandler draws its own
    # level and time columns (leaving %(levelname)s or %(asctime)s in the format
    # string printed the level twice and handed the timestamp to the message
    # highlighter, which painted it in three colours -- see UtcRichHandler), and
    # its right-hand column already gives file:line. Logger name, function, thread
    # name and thread id are dropped as console noise; the file keeps all four.
    console_formatter = UtcFormatter("%(message)s")
    stream_handlers = [h for h in logger_.handlers if isinstance(h, logging.StreamHandler)]
    if not stream_handlers:
        # handler = logging.StreamHandler()
        # handler.setLevel(level)
        # handler.setFormatter(formatter)
        # logger_.addHandler(handler)

        # omit_repeated_times=False: Rich blanks a timestamp identical to the
        # previous line's by default, which at millisecond resolution mostly does
        # not trigger -- and when it does, a line with no time at all is worse than
        # a repeated one in a log read out of order.
        rich_handler = UtcRichHandler(
            rich_tracebacks=True,
            show_time=True,
            omit_repeated_times=False,
            log_time_format=utc_log_time,
        )
        rich_handler.setLevel(level)
        rich_handler.setFormatter(console_formatter)
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
