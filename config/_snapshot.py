"""One consistent view of the configuration database.

A snapshot is **immutable and published by whole-object replacement**, never mutated in
place. That is what lets a reader hold a self-consistent view for as long as it likes
while taking no lock: an attribute read is atomic under the GIL, so a reader either sees
the old snapshot or the new one, never a half-installed mixture.

The per-collection `generations` are what the accessor memo keys on (see `_memo.py`), so
an edit to `users` does not invalidate a cached `SpecsConfig`. `generation` is the
whole-database counter, useful for "has anything changed at all" and for logging.

Both counters are **per-process and in-memory**: they start at 0 in every process and
have no meaning across processes or restarts. They are a cache-invalidation key, not an
identity for a particular configuration. Anything that needs to name a specific
configuration to a human -- a filename, a log line an operator will correlate across
machines -- must use `loaded_at`, not a generation.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

#: Where a snapshot's documents came from. "local-cache" means MongoDB could not be
#: reached and the on-disk boot cache was used instead -- always paired with
#: `degraded=True`.
SnapshotSource = Literal["mongodb", "local-cache"]


@dataclass(frozen=True)
class ConfigSnapshot:
    """An immutable view of every configuration collection, plus how it was obtained."""

    #: collection name -> the documents as read, with `_id` projected out.
    collections: dict[str, list[dict[str, Any]]]
    #: collection name -> how many times THAT collection has changed in this process.
    generations: dict[str, int]
    #: How many times any collection has changed in this process.
    generation: int
    #: collection name -> when it was last read (not when it last changed).
    loaded_at: dict[str, datetime]
    degraded: bool = False
    degraded_reason: str | None = None
    source: SnapshotSource = "mongodb"

    @classmethod
    def initial(
        cls,
        collections: dict[str, list[dict[str, Any]]],
        loaded_at: datetime,
        *,
        degraded: bool = False,
        degraded_reason: str | None = None,
        source: SnapshotSource = "mongodb",
    ) -> "ConfigSnapshot":
        """The first snapshot of a process, with every collection at generation 0."""
        return cls(
            collections=dict(collections),
            generations=dict.fromkeys(collections, 0),
            generation=0,
            loaded_at=dict.fromkeys(collections, loaded_at),
            degraded=degraded,
            degraded_reason=degraded_reason,
            source=source,
        )
