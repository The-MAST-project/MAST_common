"""Generation-keyed memoization for `Config`'s accessors.

This replaces the two TTL caches the module used to carry, both of which were inert:
`mongo_cache` wrapped a loader reachable only from `Config.__init__`, so its 60 s TTL
never caused a re-read, and `config_db_cache` wrapped a method whose body was
`return self.db` -- a timer around a live attribute reference.

A time-keyed cache is the wrong shape for configuration in both directions at once: too
eager, because it rebuilds an unchanged model every time the clock runs out, and too
lazy, because it serves a stale model for the rest of the window after a change --
including the caller's own write. Keying on the *generations of the collections the
accessor actually reads* is exact: the entry is dropped the instant one of its inputs
changes, and never dropped for any other reason.

That exactness is what makes the consumer migration affordable. `self.conf = <snapshot>`
in a constructor can become a `@property` returning a live lookup only if the lookup is
roughly a dict probe; with a TTL cache it would instead be a periodic rebuild of a
pydantic model (and, for `get_unit`, a periodic DNS lookup).

It also gives the accessors an identity property the design leans on elsewhere: within
one generation an accessor returns *the same object* every time, so an operation that
binds `conf = self.unit.unit_conf` at entry holds a stable, self-consistent view for its
whole duration with no copying and no locking. See "Operation-scoped stability" in
`claude/plans/dynamic-configuration.md`.
"""

import functools
import inspect
import threading
from collections.abc import Callable
from typing import Any

from cachetools import LRUCache

#: Bounded rather than a single slot per accessor: MAST_control builds every unit's
#: configuration in a loop, so a one-entry memo would thrash on `get_unit`. Bounded
#: rather than unbounded so entries from superseded generations fall out on their own
#: instead of accumulating for the life of the process.
MEMO_MAXSIZE = 64

_MISS = object()


def make_memo() -> tuple[LRUCache, threading.Lock]:
    """A memo store and its lock. `cachetools.LRUCache` is not itself thread-safe."""
    return LRUCache(maxsize=MEMO_MAXSIZE), threading.Lock()


def by_generation(*deps: str) -> Callable:
    """Memoize a `Config` accessor against the generations of the collections it reads.

    `deps` must name every collection the accessor consults, including indirectly --
    `get_unit` reads `units` but also `sites`, because it verifies site membership, and
    `get_users` reads `users` but also `groups`, because capabilities come from group
    membership. Under-declaring a dependency is the one way to get a stale value out of
    this: the entry would survive a change it should not have.

    The decorated function is called with an extra `_snapshot` keyword holding the exact
    snapshot the key was computed from. It MUST build its result from that snapshot
    rather than re-reading the current one, or the value would not be a function of its
    key -- two generations could be mixed into one cached model.
    """

    def decorator(func: Callable) -> Callable:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # An explicit snapshot still goes through the memo: the key carries that
            # snapshot's generations, so a caller threading one through gets the same
            # cached object as a caller that did not.
            snapshot = kwargs.pop("_snapshot", None) or self.snapshot

            # Bind so that get_unit("ns", "mast01") and get_unit(unit_name="mast01",
            # site_name="ns") produce one key and therefore one object. Without this the
            # two spellings would each get their own entry, and the "same object within a
            # generation" property would hold only per spelling.
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            call_args = tuple(sorted((k, v) for k, v in bound.arguments.items() if k not in ("self", "_snapshot")))

            key = (func.__qualname__, tuple(snapshot.generations.get(d, -1) for d in deps), call_args)

            with self._memo_lock:
                hit = self._memo.get(key, _MISS)
            if hit is not _MISS:
                return hit

            # Built outside the lock deliberately. Two threads racing on a cold key both
            # build; both results are equal, being pure functions of the same snapshot,
            # and the second write is a harmless overwrite. That is strictly better than
            # holding a lock across `get_unit`'s DNS resolution.
            value = func(self, *args, _snapshot=snapshot, **kwargs)

            with self._memo_lock:
                self._memo[key] = value
            return value

        wrapper.__config_deps__ = deps  # type: ignore[attr-defined]  # read by tests
        return wrapper

    return decorator


def memo_deps(accessor: Any) -> tuple[str, ...] | None:
    """The collections an accessor declared, or None if it is not memoized."""
    return getattr(accessor, "__config_deps__", None)
