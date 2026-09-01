"""The thread that keeps a process's configuration current.

Shape, and why:

    full reload -> open a change stream -> block on it
      on any failure: go degraded, keep re-reading at the poll interval, retry with
      capped backoff
      on invalidate/drop/rename: drop the stream and start again from a full reload

**A change-stream event is a trigger, never data.** A delete carries only
`documentKey._id`, and `_id` is projected out of every stored document, so an event
cannot be applied incrementally -- it can only say which collection to re-read. That one
constraint decides most of what follows.

It also dissolves the resume-token problem, which is why **no token is ever passed or
persisted**. pymongo already resumes transparently across ordinary network blips. The
cases it cannot cover -- `invalidate`, `ChangeStreamHistoryLost` (the oplog rolled past
our token), a failover -- all want the same response, and it is the one the outer loop
already makes: drop the stream, re-read everything, open a fresh stream from now. Since
events are triggers, missing some across that gap costs latency, not correctness.
Persisting a token across a restart would buy nothing (startup re-reads regardless) and
would add the one failure mode we currently cannot have: a stale token that fails to
resume and needs its own recovery path.

Nothing here is allowed to be fatal. The watcher is an optimisation over "restart the
service to pick up a config change"; it must outlive any failure, exactly as
`Notifier._notification_worker` does.
"""

import threading
import time
from collections.abc import Callable, Iterable

from common.mast_logging import get_logger

logger = get_logger(__name__)

#: How long the server holds an idle change-stream `getMore` before returning nothing.
#:
#: This bounds two things: how long `stop()` can take to be noticed, and how often an
#: idle watcher does a round trip. It does NOT bound how quickly a change is seen --
#: awaitData returns as soon as an event exists, measured at ~0s against `rs0`.
#:
#: Kept at 2s rather than something longer so `stop()` returns within its join timeout.
#: The cost is one no-op round trip per watcher per 2s, which awaitData parks server-side.
WATCH_AWAIT_MS = 2_000

#: While healthy, re-read everything this often anyway. A change stream can be alive and
#: silently behind, and that failure is otherwise indistinguishable from "nobody edited
#: anything" -- there is no error to see and no event to miss noticing.
SAFETY_POLL_SECONDS = 300

#: While degraded, or with no change-stream support at all, this is the retry/poll rate.
POLL_SECONDS = 60

#: Reconnection backoff, capped so an outage settles into a steady slow retry.
BACKOFF_FIRST_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 60.0


class _Backoff:
    def __init__(self, first: float = BACKOFF_FIRST_SECONDS, cap: float = BACKOFF_CAP_SECONDS):
        self._first = first
        self._cap = cap
        self._next = first

    def reset(self) -> None:
        self._next = self._first

    def next(self) -> float:
        value = self._next
        self._next = min(self._next * 2, self._cap)
        return value


class ConfigWatcher:
    """Tracks the configuration source and calls back when something changes.

    `reload_all` and `reload` do the reading and publishing; this class owns only the
    thread, the stream, and the decision about when to call them.
    """

    def __init__(
        self,
        source,
        collections: Iterable[str],
        *,
        reload_all: Callable[[], None],
        reload: Callable[[Iterable[str]], None],
        on_degraded: Callable[[str], None],
        on_healthy: Callable[[], None],
        poll_seconds: float = POLL_SECONDS,
        safety_poll_seconds: float = SAFETY_POLL_SECONDS,
    ):
        self._source = source
        self._collections = list(collections)
        self._reload_all = reload_all
        self._reload = reload
        self._on_degraded = on_degraded
        self._on_healthy = on_healthy
        self._poll_seconds = poll_seconds
        self._safety_poll_seconds = safety_poll_seconds

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_full_read = 0.0
        #: Edge-triggered logging: a weekend-long outage must not write one ERROR per
        #: retry into a DailyFileHandler. One on the way down, one on the way back up.
        self._degraded_since: float | None = None

    # ---------------- lifecycle ----------------

    def start(self) -> None:
        """Idempotent; safe to call from anywhere, including twice."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="config-watcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            # The stream's awaitData wait is bounded by WATCH_AWAIT_MS, so this returns
            # within that even if the join times out first.
            thread.join(timeout=timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ---------------- the loop ----------------

    def _run(self) -> None:
        backoff = _Backoff()
        while not self._stop.is_set():
            try:
                # Always a full read on (re)entry. After any gap -- startup, a dropped
                # stream, an outage -- this is what makes "events are triggers" safe:
                # whatever we missed is picked up here rather than inferred.
                self._full_read()
                self._mark_healthy()
                backoff.reset()

                if self._source.supports_watch():
                    self._follow_stream()
                else:
                    self._poll_until_stop()
            except Exception as ex:  # noqa: BLE001 -- the watcher must outlive anything
                self._mark_degraded(f"{type(ex).__name__}: {ex}")
                self._stop.wait(backoff.next())

    def _follow_stream(self) -> None:
        """Block on the change stream until it ends, we stop, or it raises.

        One event, one reload -- there is deliberately no "collect the rest of the burst"
        loop here. An earlier version had one, and it made every change take a full
        `WATCH_AWAIT_MS` to land: the extra `try_next()` looking for more events blocks
        for the whole idle timeout when there are none, which is the common case. Against
        `rs0` that turned a ~0s propagation into a measured 10s one.

        Nothing is lost by dropping it. pymongo returns already-buffered events without a
        round trip, so a burst still drains in quick succession; reloading a collection
        twice is two small reads and the second publishes nothing, because `_publish`
        compares documents rather than counting events; and the callbacks -- the part
        that actually wants coalescing -- are merged into a single pending change by
        `Config._enqueue_change` regardless of how many reloads produced them.
        """
        with self._source.watch(self._collections, max_await_time_ms=WATCH_AWAIT_MS) as stream:
            while not self._stop.is_set():
                change = stream.try_next()

                if change is None:
                    # Idle. Use the quiet moment for the safety poll rather than adding a
                    # second timer.
                    if time.monotonic() - self._last_full_read >= self._safety_poll_seconds:
                        self._full_read()
                    continue

                if self._ends_stream(change):
                    return  # the outer loop re-reads everything and opens a fresh stream

                name = change["ns"]["coll"]
                if name in self._collections:
                    self._reload([name])

    @staticmethod
    def _ends_stream(change) -> bool:
        kind = change.get("operationType")
        if kind in ("invalidate", "drop", "dropDatabase", "rename"):
            logger.warning(f"config: change stream ended with '{kind}'; reopening")
            return True
        return False

    def _poll_until_stop(self) -> None:
        """The no-change-stream path: re-read everything on a timer.

        There is no fingerprint here on purpose. The obvious one --
        `(estimated_document_count, last _id)`, which the fleet's own config-DB monitor
        uses -- cannot see an in-place edit to an existing document, and that is the
        dominant case: an operator changing a value. Reading all six collections is ~18 KB
        and the comparison that decides whether anything changed is exact.
        """
        while not self._stop.is_set():
            if self._stop.wait(self._poll_seconds):
                return
            self._full_read()
            self._mark_healthy()

    def _full_read(self) -> None:
        self._reload_all()
        self._last_full_read = time.monotonic()

    # ---------------- degraded transitions ----------------

    def _mark_degraded(self, reason: str) -> None:
        if self._degraded_since is None:
            self._degraded_since = time.monotonic()
            logger.error(f"config: lost contact with the configuration database: {reason}")
        else:
            logger.debug(f"config: still degraded: {reason}")
        self._on_degraded(reason)

    def _mark_healthy(self) -> None:
        if self._degraded_since is not None:
            outage = time.monotonic() - self._degraded_since
            logger.info(f"config: configuration database reachable again after {outage:.0f}s")
            self._degraded_since = None
        self._on_healthy()
