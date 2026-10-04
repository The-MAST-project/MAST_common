"""Earth-orientation (IERS) policy and table cache: pointing must not depend on the network.

astropy raises, by default, when it has to interpolate Earth-orientation values whose
predictions have gone stale. Its own remedy is to fetch a newer `finals2000A.all` first -- and
**that remedy is not sufficient here, which is the point of this module.**

It is tempting to read the 2026-10-01 incident as "the site proxy blocked the fetch". It was
not that. These machines reach the IERS data centre perfectly well with no `HTTP(S)_PROXY` in
the environment at all, because `urllib.request.getproxies()` reads the WinINET registry
settings on Windows -- verified by downloading the table with every proxy variable unset. What
actually happened is simpler and worse: the table **the server was publishing** carried
measured values only to 2026-07-31, so a *successful* download still left astropy more than 30
days past its last measured value, and it raised anyway.

So no amount of networking would have kept the mount pointing that night. Only
`auto_max_age = None` would have.

**Two independent mechanisms do this, and they are governed by different settings** (checked
against astropy 8.0.1's `utils/iers/iers.py`; worth re-checking when that pin moves):

1. `IERS_Auto._check_interpolate_indices` raises a plain `ValueError` when the requested time
   lies in the table's predictive span AND `now - predictive_mjd > conf.auto_max_age`
   (default 30 days). **This raise is not governed by `iers_degraded_accuracy` at all.** The
   only thing that disables it is `auto_max_age = None`, which astropy turns into
   `np.finfo(float).max` so the comparison can never be true. *This is the one that stopped
   the mount on 2026-10-01.*
2. `IERS._check_interpolate_indices`, the base-class check, raises `IERSRangeError` when
   times fall outside the table's range altogether. That one *is* governed by
   `iers_degraded_accuracy`, whose default is `"error"`.

Both are set, for different reasons: (1) because a stale table must degrade accuracy rather
than stop a telescope, and (2) because with downloads off a cached table eventually ages past
the ~1 year of predictions it carries, at which point every transform is out of range.

It is not hypothetical. On 2026-10-01 it took down the first mount-stability campaign run:
every visit died before its slew with "predictive values that are more than 30.0 days old",
the mount never moved, and because the campaign is built so one bad cell cannot end the
night, a 100% failure rate looked exactly like a bad cell (MAST_unit#281).

The exposure is fleet-wide, not campaign-specific: `common.config.site.Site.observing_window`
calls `astroplan.Observer.sun_set_time()`, which transforms the Sun into the horizon frame
and so needs UT1-UTC -- and astroplan is reached by every `import common.config`. So the
policy lives here, in `common`, not in one service's entry point. See MAST_common#139.

So: **never fetch, never raise, degrade loudly.** What is given up is UT1-UTC prediction
error -- tens of milliseconds over the months a cached table stays usable, far below this
mount's pointing residual, and the science path plate-solves regardless.

Two things this module deliberately does NOT rely on:

* **astropy's download cache.** It is keyed by URL hash (opaque to an operator) and lives
  under the running account's home (so it moves with whichever user starts the service).
  Worse, it cannot be trusted to serve what it holds: on 2026-10-04, in one process,
  `IERS_A.open(<the cache file for conf.iers_auto_url>)` returned 19999 rows with
  `predictive_mjd` 61315 while `IERS_Auto.open()` returned 19941 rows with 61252. Whatever
  resolution astropy applies there, it is not "read the cached copy of the configured URL",
  so this module loads a named file explicitly and hands astropy the result.
* **`auto_max_age` as a staleness signal.** With the policy applied it is `None`, which is
  the point -- nothing may raise. Staleness is then OURS to notice, which is what
  `lag_days()` and `log_lag()` are for.
"""

import contextlib
import os
import platform
import re
import tempfile
import threading
from datetime import UTC, datetime, timedelta

from astropy.utils import iers
from astropy.utils.data import download_file
from astropy.utils.iers import IERS_A

from common.mast_logging import get_logger

if platform.system() == "Windows":
    import win32api
    import win32event
    import winerror

logger = get_logger(__name__)

#: What a MAST process sets on `astropy.utils.iers.conf`, and why each one:
#:   auto_download          -- a transform must never wait on, or fail because of, a fetch
#:   auto_max_age           -- None is what actually disables the stale-prediction ValueError;
#:                             see mechanism (1) in the module docstring
#:   iers_degraded_accuracy -- warn rather than raise once a table ages past its predictions;
#:                             mechanism (2), and a different code path from auto_max_age
POLICY: dict[str, object] = {
    "auto_download": False,
    "auto_max_age": None,
    "iers_degraded_accuracy": "warn",
}

#: Set to 1/true/yes to let astropy fetch for itself -- for a development box that has
#: working internet and would rather have a current table than a reproducible one. An
#: environment variable rather than a config-DB setting on purpose: `Config()` can reach
#: `Site.observing_window()`, so a DB-driven policy would make "can this process point?"
#: depend on a DB read that may itself need the policy already applied.
ALLOW_DOWNLOAD_ENV = "MAST_IERS_ALLOW_DOWNLOAD"

#: Beyond this, the table's predictions are old enough that astropy would have refused to
#: interpolate from them had we left `iers_degraded_accuracy` at its default. Not a number
#: of ours: it is astropy's own `auto_max_age` default, and therefore the only threshold
#: that corresponds to a real change in behaviour.
STALE_AFTER_DAYS = 30.0

#: `PREFIX + <compact UTC> + "-pm" + <predictive MJD> + SUFFIX`.
#:
#: The fetch stamp makes the newest copy findable by a name sort, exactly as the config boot
#: cache does. The predictive MJD is in the name because parsing the table to find it costs
#: ~510 ms for ~20k rows, and the lag is wanted on every staleness check and every decision
#: about whether to fetch -- far too often to pay that.
PREFIX = "iers-finals2000A-"
SUFFIX = ".all"
LATEST = "latest"

#: Each copy is ~3.5 MB, so this is ~18 MB of history. Fewer than the config cache's 10:
#: these are much larger, and unlike configuration documents an old Earth-orientation table
#: has no forensic value beyond "what were we interpolating from".
KEEP_COPIES = 5

#: Anchored so a leftover `.tmp` from an interrupted write is neither loaded nor pruned.
_NAME_RE = re.compile(rf"^{re.escape(PREFIX)}(\d{{8}}T\d{{6}}Z)-pm(\d+){re.escape(SUFFIX)}$")

#: MJD zero. Used to turn "now" into an MJD without `astropy.time`, which is a heavier
#: import than this needs and -- more to the point -- is itself a consumer of the IERS
#: machinery this module configures.
_MJD_EPOCH = datetime(1858, 11, 17, tzinfo=UTC)

_configured = False
_last_lag_warning: datetime | None = None


def _stamp(when: datetime) -> str:
    """A filename-safe UTC stamp, matching `config/_cache.py`'s.

    Not `common.utils.time_stamp()`: that returns ISO-8601, whose colons are illegal in
    Windows filenames, and the units are the Windows machines.
    """
    return when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def now_mjd(when: datetime | None = None) -> float:
    """`when` (default: now) as a Modified Julian Date.

    Days of UTC since the MJD epoch, which ignores leap seconds. That is accurate to well
    under a second and the only consumer here compares whole days of table lag, so the
    approximation is far below the resolution of the question being asked.
    """
    when = when or datetime.now(UTC)
    return (when.astimezone(UTC) - _MJD_EPOCH).total_seconds() / 86400.0


def configure_astropy(*, force: bool = False) -> bool:
    """Apply `POLICY` to `astropy.utils.iers.conf`. Returns True if it applied it.

    Idempotent: the second and later calls are no-ops and return False, so the explicit
    call from a service's app lifespan and the defensive call from inside `common`'s own
    IERS-dependent code paths cannot fight each other.

    **Call this before any coordinate transform**, and therefore before `Config()`, which
    can reach `Site.observing_window()`.

    Every key is checked against `conf.keys()` before being set, because
    `astropy.utils.iers.conf` **silently accepts an unknown attribute** -- verified on
    astropy 8.0.1 -- storing it as an ordinary instance attribute that changes nothing. A
    bare assignment against an astropy that lacks one of these keys would therefore either
    do nothing silently, or (for a renamed key) leave the default in force while reading as
    though the policy were applied. MAST_spec runs astropy 7 while MAST_unit runs 8, and
    `requirements.txt` pins nothing on purpose, so this is a live difference and not
    future-proofing.
    """
    global _configured
    if _configured and not force:
        return False

    policy = dict(POLICY)
    if os.environ.get(ALLOW_DOWNLOAD_ENV, "").strip().lower() in ("1", "true", "yes"):
        policy["auto_download"] = True
        logger.info(f"iers: {ALLOW_DOWNLOAD_ENV} is set; allowing astropy to fetch for itself")

    known = set(iers.conf.keys())
    for name, value in policy.items():
        if name not in known:
            logger.warning(f"iers: astropy has no conf setting '{name}'; not set (astropy version difference?)")
            continue
        setattr(iers.conf, name, value)

    _configured = True
    logger.info(
        "iers: policy applied -- "
        f"auto_download={iers.conf.auto_download}, "
        f"auto_max_age={iers.conf.auto_max_age}, "
        f"iers_degraded_accuracy={getattr(iers.conf, 'iers_degraded_accuracy', 'n/a')}"
    )
    return True


def is_configured() -> bool:
    """Whether `configure_astropy()` has run in this process."""
    return _configured


def cache_files(directory: str) -> list[str]:
    """Every cached table in `directory`, oldest first.

    Sorted by name, which is chronological because the stamp is compact UTC -- and so needs
    no `readlink`, which is why nothing here follows `latest` (on Windows that is a copy,
    symlinks needing a privilege the service account does not have).
    """
    try:
        return sorted(name for name in os.listdir(directory) if _NAME_RE.match(name))
    except OSError:
        return []


def parse_name(name: str) -> tuple[datetime, float] | None:
    """`(fetched_at, predictive_mjd)` from a cache filename, or None if it is not one."""
    match = _NAME_RE.match(os.path.basename(name))
    if not match:
        return None
    try:
        fetched_at = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return fetched_at, float(match.group(2))


def newest(directory: str) -> tuple[str, datetime, float] | None:
    """The newest cached table as `(path, fetched_at, predictive_mjd)`, or None.

    "Newest" is by fetch stamp, not by predictive MJD: `write()` already refuses a copy
    whose predictions do not advance, so the newest fetch is also the most current table.
    """
    names = cache_files(directory)
    if not names:
        return None
    name = names[-1]
    parsed = parse_name(name)
    if parsed is None:  # pragma: no cover -- cache_files only yields matching names
        return None
    fetched_at, predictive_mjd = parsed
    return os.path.join(directory, name), fetched_at, predictive_mjd


def lag_days(directory: str, when: datetime | None = None) -> float | None:
    """How far behind the cached table's predictions are, in days. None if there is no cache.

    This -- not the fetch timestamp -- is the staleness measure. On 2026-09-30 a fetch
    succeeded, was cached, and the table read back 65 days behind: a successful download is
    not a successful refresh, so anything gated on "when did we last succeed" can sit quiet
    while the table is unusable.
    """
    found = newest(directory)
    if found is None:
        return None
    _, _, predictive_mjd = found
    return now_mjd(when) - predictive_mjd


def is_stale(directory: str, when: datetime | None = None) -> bool:
    """True if there is no cached table, or its lag exceeds `STALE_AFTER_DAYS`."""
    lag = lag_days(directory, when)
    return lag is None or lag > STALE_AFTER_DAYS


def write(directory: str, content: bytes, predictive_mjd: float, when: datetime | None = None) -> str | None:
    """Store `content` as a new cached table. Returns its path, or None if it was not stored.

    Refuses a candidate whose `predictive_mjd` does not **advance** on what is already
    cached. That is the lesson of 2026-09-30, where a fetch returned HTTP 200 and left the
    cache no fresher than before, with nothing to say so. Keeping the older copy also means
    a bad upstream day can never cost us a good table.

    Best-effort otherwise: a read-only directory or a full disk is a reason to carry on with
    the table we have, not a reason to stop a telescope. Nothing here raises.
    """
    found = newest(directory)
    if found is not None and predictive_mjd <= found[2]:
        logger.info(
            f"iers: not storing a table whose predictions do not advance "
            f"(candidate predictive_mjd={predictive_mjd:.0f}, cached={found[2]:.0f})"
        )
        return None

    when = when or datetime.now(UTC)
    path = os.path.join(directory, f"{PREFIX}{_stamp(when)}-pm{predictive_mjd:.0f}{SUFFIX}")
    try:
        os.makedirs(directory, exist_ok=True)
        # Same directory as the target, so os.replace is a rename and not a copy, and a
        # reader picking the newest by name can never see a partial file: the name only
        # exists once the content is complete.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=PREFIX, suffix=SUFFIX + ".tmp")
        try:
            with os.fdopen(fd, "wb") as fp:
                fp.write(content)
                fp.flush()
                os.fsync(fp.fileno())  # os.replace is atomic; it does not imply durable
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as ex:
        logger.warning(f"iers: could not write '{path}': {ex}")
        return None

    _refresh_latest(directory, os.path.basename(path))
    _prune(directory)
    logger.info(f"iers: stored '{os.path.basename(path)}' (predictions to MJD {predictive_mjd:.0f})")
    return path


def _refresh_latest(directory: str, newest_name: str) -> None:
    """Point `latest` at `newest_name`, by symlink where that is allowed and by copy where it
    is not.

    A symlink on Windows needs Developer Mode or SeCreateSymbolicLinkPrivilege, which the
    units' service account does not have, so the copy fallback is the normal path there
    rather than an edge case -- as it is for the config boot cache.

    `latest` is for a person reading the directory. Nothing in the code follows it, which is
    also why the copy costing a duplicate ~3.5 MB is acceptable.
    """
    link = os.path.join(directory, LATEST)
    try:
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(newest_name, link)
    except (OSError, NotImplementedError):
        try:
            with open(os.path.join(directory, newest_name), "rb") as src, open(link, "wb") as dst:
                dst.write(src.read())
        except OSError as ex:
            logger.warning(f"iers: could not refresh '{link}': {ex}")


def _prune(directory: str, keep: int = KEEP_COPIES) -> None:
    """Delete all but the newest `keep` copies. Housekeeping, never fatal."""
    names = cache_files(directory)
    for name in names[: max(0, len(names) - keep)]:
        try:
            os.unlink(os.path.join(directory, name))
        except OSError as ex:
            logger.warning(f"iers: could not prune '{name}': {ex}")


def read_predictive_mjd(path: str) -> float | None:
    """`predictive_mjd` of the table at `path` -- the last MJD with a MEASURED value.

    This is the expensive operation the filename exists to avoid: ~510 ms for ~20k rows. Use
    it when ingesting a candidate, not when checking staleness.

    It is also the only validation a candidate gets, and it is a real one: a truncated
    download, an HTML error page or a proxy's captive-portal response all fail to parse.
    """
    try:
        return float(IERS_A.open(path).meta["predictive_mjd"])
    except Exception as ex:  # noqa: BLE001 -- anything unparseable is simply not a table
        logger.warning(f"iers: could not read a predictive MJD from '{path}': {ex}")
        return None


def load_into_astropy(directory: str) -> bool:
    """Hand the newest cached table to astropy as the Earth-orientation table it must use.

    Explicit, rather than letting `IERS_Auto` resolve a table for itself, because it cannot
    be trusted to serve what its own cache holds -- see this module's docstring for the two
    row counts measured in one process on 2026-10-04.

    Returns False when there is no cache or the table will not parse, in which case astropy
    keeps whatever it would have used anyway. With the policy applied that degrades accuracy
    and warns; it does not raise.
    """
    found = newest(directory)
    if found is None:
        logger.warning(f"iers: no cached table in '{directory}'; leaving astropy to its own devices")
        return False

    path, fetched_at, predictive_mjd = found
    try:
        iers.earth_orientation_table.set(IERS_A.open(path))
    except Exception as ex:  # noqa: BLE001 -- a bad table must not stop a telescope
        logger.warning(f"iers: could not load '{os.path.basename(path)}': {ex}")
        return False

    logger.info(
        f"iers: using '{os.path.basename(path)}' -- fetched {fetched_at.isoformat()}, "
        f"predictions to MJD {predictive_mjd:.0f}, lag {now_mjd() - predictive_mjd:.1f} d"
    )
    return True


def log_lag(directory: str, when: datetime | None = None, every: timedelta = timedelta(days=1)) -> None:
    """Report the cached table's lag: INFO while fresh, WARNING once per `every` while stale.

    Rate-limited on purpose. A warning emitted on every fetch decision -- every 30 minutes
    for a month -- trains people to ignore it, which would leave us worse off than the status
    field MAST_common#139 decided not to add. Log-only was chosen there, so this line is the
    whole alarm and it has to stay worth reading.

    ASCII only: the daily log file is UTF-8 now, but MAST_common#63 is the reminder that its
    consumers are not all safe with anything else.
    """
    global _last_lag_warning
    when = when or datetime.now(UTC)
    lag = lag_days(directory, when)

    if lag is None:
        message = f"iers: no cached Earth-orientation table in '{directory}'"
    else:
        message = f"iers: Earth-orientation table is {lag:.1f} days behind its last measured value"

    if lag is not None and lag <= STALE_AFTER_DAYS:
        logger.info(message)
        return

    if _last_lag_warning is not None and when - _last_lag_warning < every:
        return
    _last_lag_warning = when
    logger.warning(
        f"{message} (over the {STALE_AFTER_DAYS:.0f}-day mark astropy would have refused to "
        f"interpolate past). Pointing still works, at degraded accuracy."
    )


# ------------------------------------------------------------------ the refresher --

#: How often to try while the cached table is within `STALE_AFTER_DAYS`. The upstream product
#: is IERS Bulletin A, issued weekly (Thursdays) with measured values lagging ~1-2 days, so
#: daily is already generous; more often would be pure waste.
FETCH_INTERVAL_FRESH = timedelta(days=1)

#: How often to try while it is stale. Eager on purpose: we are degraded, the machine may only
#: get a brief window of connectivity, and catching one is worth more than politeness.
FETCH_INTERVAL_STALE = timedelta(minutes=30)

#: Never two attempts closer together than this, whatever the lag says. A backstop against a
#: tight loop if a clock moves or the marker cannot be written.
MIN_FETCH_INTERVAL = timedelta(minutes=5)

#: Records the last ATTEMPT -- success or failure -- as an ISO-8601 UTC line.
#:
#: Attempts, not successes, and on disk rather than in memory. Two reasons, both learned: a
#: machine offline for a week never records a success, so a success-gated refresher would
#: retry at full rate for ever; and several MAST services share a machine, so the interval has
#: to survive one of them restarting.
ATTEMPT_MARKER = "last-attempt"

#: One refresher per machine. The Windows name is global because named objects there are
#: scoped per *session*: a service in session 0 and an interactive process in session 1 would
#: not see a bare name even running as the same account. Same reasoning, and the same
#: mechanism, as `Filer._take_sweep_guard`.
GUARD_NAME = "Global\\mast_iers_refresher"

#: How long a single download may take. The table is ~3.8 MB.
FETCH_TIMEOUT_SECONDS = 120

_guard: object | None = None
_refresher: threading.Thread | None = None
_stop = threading.Event()


def last_attempt(directory: str) -> datetime | None:
    """When a fetch was last attempted on this machine, or None."""
    try:
        with open(os.path.join(directory, ATTEMPT_MARKER), encoding="utf-8") as fp:
            return datetime.fromisoformat(fp.read().strip())
    except (OSError, ValueError):
        return None


def record_attempt(directory: str, when: datetime | None = None) -> None:
    """Note that a fetch was attempted. Best-effort; never raises."""
    when = when or datetime.now(UTC)
    try:
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, ATTEMPT_MARKER), "w", encoding="utf-8") as fp:
            fp.write(when.astimezone(UTC).isoformat())
    except OSError as ex:
        logger.warning(f"iers: could not record a fetch attempt in '{directory}': {ex}")


def should_fetch(directory: str, when: datetime | None = None) -> bool:
    """Whether to attempt a fetch now.

    **The need is decided by the table's lag; the rate is limited by the last attempt.**
    Keeping those separate is the whole point. On 2026-09-30 a fetch succeeded and left the
    cache 65 days behind, so anything gated on "we succeeded recently" would have gone quiet
    exactly when it most needed to try again.
    """
    when = when or datetime.now(UTC)
    attempted = last_attempt(directory)
    if attempted is not None:
        since = when - attempted
        if since < MIN_FETCH_INTERVAL:
            return False
        wanted = FETCH_INTERVAL_STALE if is_stale(directory, when) else FETCH_INTERVAL_FRESH
        if since < wanted:
            return False
    return True


def fetch_once(directory: str, url: str | None = None, timeout: int = FETCH_TIMEOUT_SECONDS) -> str | None:
    """Attempt one fetch. Returns the stored path, or None if nothing was stored.

    None covers three different outcomes, all survivable and all logged: the download failed,
    what came back is not a table, or it is a table whose predictions do not advance on what we
    already have -- `write()` refuses that.

    `download_file` rather than `urllib` directly, because it is what demonstrably reaches the
    IERS data centre from these machines. **No proxy configuration is needed or wanted**:
    `urllib.request.getproxies()` reads the WinINET registry settings on Windows, which is how
    a MAST service reaches the internet with no `HTTP(S)_PROXY` in its environment. Note that
    `MAST_unit`'s `start_supporting_processes()` deliberately DELETES those variables so that
    talking to PWI4 on 127.0.0.1 is not proxied -- a fetcher that depended on them could
    therefore never work inside the unit.

    `cache=False`: astropy's own download cache is bypassed entirely. This module's cache is
    the one that matters, and astropy's has been observed not to serve what it holds.
    """
    url = url or iers.conf.iers_auto_url
    record_attempt(directory)

    try:
        downloaded = download_file(url, cache=False, timeout=timeout)
    except Exception as ex:  # noqa: BLE001 -- offline, DNS, TLS, 404: one answer covers them all
        logger.info(f"iers: fetch from '{url}' did not succeed ({type(ex).__name__}: {ex}); keeping the cached table")
        return None

    try:
        predictive_mjd = read_predictive_mjd(downloaded)
        if predictive_mjd is None:
            return None
        with open(downloaded, "rb") as fp:
            content = fp.read()
    except OSError as ex:
        logger.warning(f"iers: could not read the downloaded table: {ex}")
        return None
    finally:
        with contextlib.suppress(OSError):
            os.unlink(downloaded)

    return write(directory, content, predictive_mjd)


def refresh_if_needed(directory: str) -> str | None:
    """`fetch_once` if `should_fetch` says so. Returns the stored path, or None."""
    if not should_fetch(directory):
        return None
    return fetch_once(directory)


def _linux_guard_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".mast-iers-refresher.lock")


def _take_guard() -> bool:
    """One refresher per machine, held by an ephemeral kernel object -- nothing on disk.

    Released by the kernel if this process dies, so a crashed service cannot lock the machine
    out of refreshing. Failing to take it means skipping, never proceeding unguarded: two
    processes fetching 3.8 MB and racing to promote it is waste rather than corruption, but it
    is waste with no upside.
    """
    global _guard
    if platform.system() == "Windows":
        try:
            _guard = win32event.CreateMutex(None, False, GUARD_NAME)
            return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS
        except Exception as ex:  # noqa: BLE001 -- e.g. ACCESS_DENIED unelevated
            logger.info(f"iers: could not take the refresher guard ({ex}); not refreshing in this process")
            return False

    import fcntl

    try:
        _guard = os.open(_linux_guard_path(), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(_guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as ex:
        logger.info(f"iers: could not take the refresher guard ({ex}); not refreshing in this process")
        return False


def _refresher_loop(directory: str, poll: timedelta) -> None:
    while not _stop.is_set():
        try:
            refresh_if_needed(directory)
            log_lag(directory)
        except Exception:
            logger.exception("iers: refresher pass failed; continuing")
        _stop.wait(poll.total_seconds())


def start_iers_refresher(directory: str, poll: timedelta = MIN_FETCH_INTERVAL) -> bool:
    """Start the per-machine IERS refresher. Returns True if this process started it.

    **Call once, from a service's app lifespan** -- beside `Filer.start_product_relocation_sweep`,
    and for the reasons that one gives for living there. Deliberately NOT started from
    `Config()`'s constructor, even though that is the one thing every service builds exactly
    once: the test suite, provisioning scripts, one-shot CLI tools and every ad-hoc `python -c`
    build a `Config` too, and none of them should spawn a network thread. An app lifespan is
    entered only by an actual service.

    Returns False -- having done nothing -- when this process already runs one, when another
    process on this machine holds the guard, or when the guard cannot be taken. Never raises.

    Asynchronous, so it cannot help the transform that happens next; it improves the *next*
    process start. `configure_astropy()` and `load_into_astropy()` are what make the current
    process safe, and they are separate calls for exactly that reason.
    """
    global _refresher
    if _refresher is not None and _refresher.is_alive():
        return False
    if not _take_guard():
        logger.info("iers: another process on this machine is refreshing; skipping")
        return False

    _stop.clear()
    _refresher = threading.Thread(name="iers-refresher", target=_refresher_loop, args=(directory, poll), daemon=True)
    _refresher.start()
    logger.info(f"iers: refresher started, polling every {poll}")
    return True


def _release_guard() -> None:
    """Drop the machine-wide guard so another process -- or this one again -- can take it.

    `Filer`'s sweep guard is deliberately held for the life of the process, because a
    relocation sweep happens once. A refresher is different: it is long-lived and stoppable,
    so holding the guard past `stop_iers_refresher()` would leave the machine with nothing
    refreshing and no way for anything to start, until every holder exited.
    """
    global _guard
    if _guard is None:
        return
    try:
        if platform.system() == "Windows":
            _guard.Close()  # type: ignore[attr-defined]  -- a pywin32 handle
        else:
            os.close(_guard)  # type: ignore[arg-type]  -- an fd; closing drops the flock
    except Exception as ex:  # noqa: BLE001 -- releasing a guard must not raise on a shutdown path
        logger.info(f"iers: could not release the refresher guard ({ex})")
    finally:
        _guard = None


def stop_iers_refresher(timeout: float = 5.0) -> None:
    """Stop the refresher, release the guard, and wait briefly for the thread to end.

    For orderly shutdown and for tests. Safe to call when nothing is running.
    """
    _stop.set()
    if _refresher is not None:
        _refresher.join(timeout=timeout)
    _release_guard()
