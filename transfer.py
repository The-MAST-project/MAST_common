"""In-process orchestration + observability for the RAM-disk -> shared transfer.

`TransferTracker` owns the *in-process* side of moving acquisition products from
the volatile RAM disk to the shared store: move serialization, the background
worker, all transfer logging, the in-flight registry, per-tag reconciliation, and
completion notifications.

It is deliberately **not a source of truth.** Correctness comes from the
`.part`/atomic-rename contract on disk (see `common.filer`): a product is complete
only once its final name exists, and the destination is published atomically. The
tracker accelerates and records; on disagreement -- or after a crash, when the
tracker starts empty -- the filesystem wins. The actual move mechanics
(`ready`/`publish`) are injected by `Filer`, so this module never touches the disk
directly and never imports `filer` (no cycle).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import Counter
from dataclasses import dataclass
from enum import Enum, auto


class TransferState(Enum):
    WRITING = auto()  # producer is writing the .part (via Filer.atomic_path)
    READY = auto()    # final name published on RAM; awaiting / mid move
    MOVING = auto()   # being published to the shared store
    MOVED = auto()    # persisted to the shared store (terminal)
    FAILED = auto()   # write or move failed (terminal)


_TERMINAL = (TransferState.MOVED, TransferState.FAILED)


@dataclass
class TransferRecord:
    path: str
    tag: str | None
    state: TransferState
    t_started: float
    t_ended: float | None = None
    nbytes: int = 0
    error: str | None = None


class TransferTracker:
    _instance: "TransferTracker | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls, logger: logging.Logger | None = None) -> "TransferTracker":
        """Process-wide singleton. The first caller (or the first with a logger)
        sets the logger; later callers reuse the same tracker."""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(logger)
            elif logger is not None and cls._instance.logger is None:
                cls._instance.logger = logger
            return cls._instance

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger
        self.move_lock = threading.Lock()      # serializes the actual publishes
        self._cond = threading.Condition()     # guards _active/_history; notifies waiters
        self._active: dict[str, TransferRecord] = {}
        self._history: list[TransferRecord] = []
        self._max_history = 512
        self._q: "queue.Queue" = queue.Queue()
        self._worker: threading.Thread | None = None

    # --- logging -------------------------------------------------------------
    def _log(self, level: int, msg: str):
        if self.logger:
            self.logger.log(level, msg)
        elif level >= logging.WARNING:
            print(msg)

    # --- state bookkeeping ---------------------------------------------------
    @staticmethod
    def _key(path) -> str:
        # Canonical record key: callers mix str(Path) (backslashes on Windows)
        # and Path.as_posix() (forward slashes) for the same file; normalize so a
        # file's write and its move land on one lifecycle record.
        return str(path).replace("\\", "/")

    def _set(self, path, tag, state, *, nbytes=0, error=None, t_ended=None):
        path = self._key(path)
        with self._cond:
            prev = self._active.get(path)
            rec = TransferRecord(
                path=path,
                tag=tag or (prev.tag if prev else None),
                state=state,
                t_started=prev.t_started if prev else time.monotonic(),
                t_ended=t_ended,
                nbytes=nbytes or (prev.nbytes if prev else 0),
                error=error,
            )
            if state in _TERMINAL:
                self._active.pop(path, None)
                self._history.append(rec)
                if len(self._history) > self._max_history:
                    del self._history[: -self._max_history]
            else:
                self._active[path] = rec
            self._cond.notify_all()
        return rec

    # --- write lifecycle (called by Filer.atomic_path) -----------------------
    def begin_write(self, path, tag=None):
        self._set(str(path), tag, TransferState.WRITING)
        self._log(logging.DEBUG, f"transfer: writing '{path}'" + (f" [tag={tag}]" if tag else ""))

    def commit_write(self, path):
        self._set(str(path), None, TransferState.READY)
        self._log(logging.DEBUG, f"transfer: ready '{path}'")

    def abort_write(self, path, error):
        self._set(str(path), None, TransferState.FAILED, error=str(error), t_ended=time.monotonic())
        self._log(logging.ERROR, f"transfer: write FAILED '{path}': {error}")

    # --- move lifecycle ------------------------------------------------------
    def run_move(self, src, dst, tag, ready_fn, publish_fn) -> bool:
        """Synchronously publish one product. `ready_fn(src)->bool` is the
        filesystem existence gate (the source of truth); `publish_fn(src,dst)->int`
        does the atomic move and returns bytes moved. Owns state + logging; the
        existence wait is done outside the move lock so a slow source does not
        stall other movers."""
        src, dst = str(src), str(dst)
        self._set(src, tag, TransferState.MOVING)
        if not ready_fn(src):
            self._set(src, tag, TransferState.FAILED, error="source not found", t_ended=time.monotonic())
            self._log(logging.ERROR, f"transfer: move FAILED '{src}': source not found")
            return False
        with self.move_lock:
            t0 = time.monotonic()
            try:
                nbytes = publish_fn(src, dst)
                self._set(src, tag, TransferState.MOVED, nbytes=nbytes, t_ended=time.monotonic())
                self._log(logging.INFO, f"transfer: persisted '{src}' -> '{dst}' ({nbytes} bytes, {time.monotonic() - t0:.2f}s)")
                return True
            except Exception as e:
                self._set(src, tag, TransferState.FAILED, error=str(e), t_ended=time.monotonic())
                self._log(logging.ERROR, f"transfer: move FAILED '{src}' -> '{dst}': {e}")
                return False

    def submit_move(self, src, dst, tag, ready_fn, publish_fn):
        """Queue a move onto the single background worker (FIFO, serialized)."""
        src, dst = str(src), str(dst)
        self._set(src, tag, TransferState.READY)
        self._q.put((src, dst, tag, ready_fn, publish_fn))
        self._ensure_worker()

    def _ensure_worker(self):
        with self._cond:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._drain, name="transfer-worker", daemon=True)
                self._worker.start()

    def _drain(self):
        while True:
            src, dst, tag, ready_fn, publish_fn = self._q.get()
            try:
                self.run_move(src, dst, tag, ready_fn, publish_fn)
            finally:
                self._q.task_done()

    # --- notifications / await ----------------------------------------------
    def wait_for(self, path, timeout=None) -> bool:
        """Block until `path` is no longer in flight (moved or failed). Returns
        immediately if the tracker has no record of it (unknown / already done /
        post-restart) -- callers that need certainty must check the filesystem."""
        path = self._key(path)
        with self._cond:
            return self._cond.wait_for(lambda: path not in self._active, timeout=timeout)

    def wait_for_tag(self, tag, timeout=None, log_summary=True) -> bool:
        """Block until nothing tagged `tag` is in flight, then (optionally) log a
        per-tag reconciliation summary."""
        with self._cond:
            done = self._cond.wait_for(
                lambda: not any(r.tag == tag for r in self._active.values()), timeout=timeout
            )
        if log_summary:
            self.log_tag_summary(tag)
        return done

    def log_tag_summary(self, tag):
        with self._cond:
            moved = [r for r in self._history if r.tag == tag and r.state == TransferState.MOVED]
            failed = [r for r in self._history if r.tag == tag and r.state == TransferState.FAILED]
        total = len(moved) + len(failed)
        level = logging.WARNING if failed else logging.INFO
        self._log(level, f"transfer: tag={tag} persisted {len(moved)}/{total}" + (f", {len(failed)} FAILED" if failed else ""))

    # --- status snapshot (for a /status endpoint, and tests) -----------------
    def snapshot(self) -> dict:
        with self._cond:
            active = list(self._active.values())
            history = list(self._history)
        return {
            "in_flight": dict(Counter(r.state.name for r in active)),
            "in_flight_paths": [r.path for r in active],
            "moved": sum(1 for r in history if r.state == TransferState.MOVED),
            "failed": sum(1 for r in history if r.state == TransferState.FAILED),
            "moved_bytes": sum(r.nbytes for r in history if r.state == TransferState.MOVED),
        }
