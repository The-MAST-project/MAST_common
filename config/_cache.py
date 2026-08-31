"""The configuration boot cache: last-known-good documents on local disk.

This is **not** the local-JSON config backend that DECISIONS [2026-06-21] deleted. That
was a *source* -- a file you could edit, with a reader that would honour it. This is a
cache, and three properties keep the distinction real:

1. It is written only by the watcher, and only from a successful MongoDB read.
2. It is read only at startup, and only after the MongoDB read has already failed.
3. Editing it cannot reach a running system that can reach MongoDB, because the first
   successful read overwrites it.

Its purpose is narrow and concrete: a unit whose controller is down must still boot far
enough to park its mount and close its covers. Before this, `Config()` raised and the
service exited, so nssm restarted a process that died before it could listen -- a running
service, an unanswered port, and a traceback the operator had to go find (MAST_common#82).

Nothing here raises. A read-only directory, a full disk and a corrupt file are all
reasons to carry on without a boot cache, not reasons to stop a telescope.
"""

import contextlib
import json
import os
import re
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from common.mast_logging import get_logger

logger = get_logger(__name__)

#: Bumped only for a change the reader cannot handle. A cache written by a newer schema
#: is rejected rather than guessed at.
SCHEMA_VERSION = 1

#: How many copies to keep. They are ~18 KB each, so this is ~180 KB of history that has
#: repeatedly turned out to be the only record of what a machine actually had loaded.
KEEP_COPIES = 10

PREFIX = "config-db-cache-"
SUFFIX = ".json"
LATEST = "latest"

#: `PREFIX + <compact UTC> + SUFFIX`. Anchored so a leftover `.tmp` from an interrupted
#: write is neither loaded nor pruned.
_NAME_RE = re.compile(rf"^{re.escape(PREFIX)}\d{{8}}T\d{{6}}Z{re.escape(SUFFIX)}$")


def _stamp(when: datetime) -> str:
    """A filename-safe UTC stamp.

    `common.utils.time_stamp()` cannot be reused: it returns an ISO-8601 string, whose
    colons are illegal in Windows filenames -- and the units are the Windows machines.
    `paths.py` already uses dashes for the same reason.
    """
    return when.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _drop_unserializable(value: Any) -> None:
    """`json.dump(default=...)` hook: drop what JSON cannot carry, and say so.

    Nothing in the configuration database needs this today -- every collection encodes
    as-is, with no BSON `Binary` (the user pictures that were the only such field were
    removed on 2026-08-31), no `datetime`, and no `ObjectId` because `_id` is projected
    out at read time. It is insurance against a future field, and it drops rather than
    round-trips deliberately: a boot cache exists to get a machine running, not to be a
    faithful backup, and a value no consumer can read is not worth an encoding scheme.
    """
    logger.warning(f"config cache: dropping a value JSON cannot represent ({type(value).__name__})")
    return None


def cache_files(directory: str) -> list[str]:
    """Every cache copy in `directory`, oldest first.

    Sorted by name, which is chronological because the stamp is compact UTC. That is
    also why nothing here follows `latest`: on Windows `latest` is a *copy* (symlinks
    need a privilege a service account does not have), so it cannot say which copy it
    is, and a sort needs no privilege and no readlink.
    """
    try:
        return sorted(name for name in os.listdir(directory) if _NAME_RE.match(name))
    except OSError:
        return []


def write(
    directory: str,
    collections: dict[str, list[dict[str, Any]]],
    *,
    mongo_uri: str,
    database: str,
    generation: int,
    written_by: str,
    when: datetime | None = None,
) -> str | None:
    """Write one cache copy, refresh `latest`, prune. Returns its path, or None.

    Best-effort throughout: every failure is logged and swallowed.
    """
    when = when or datetime.now(UTC)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "mongo_uri": mongo_uri,
        "database": database,
        "written_at": when.astimezone(UTC).isoformat(),
        "written_by": written_by,
        # Recorded but never used to name the file: the generation is a per-process
        # counter that restarts at 0, so two machines would write different content
        # under one name. It is here because it is useful when reading a log beside a
        # cache copy, where the process context is known.
        "generation": generation,
        "collections": collections,
    }

    path = os.path.join(directory, f"{PREFIX}{_stamp(when)}{SUFFIX}")
    try:
        os.makedirs(directory, exist_ok=True)
        # Same directory as the target, so os.replace is a rename and not a copy, and so
        # a reader picking the newest by name can never see a partial file: the name only
        # exists once the content is complete.
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=PREFIX, suffix=SUFFIX + ".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fp:
                json.dump(payload, fp, default=_drop_unserializable)
                fp.flush()
                os.fsync(fp.fileno())  # os.replace is atomic; it does not imply durable
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as ex:
        logger.warning(f"config cache: could not write '{path}': {ex}")
        return None

    _refresh_latest(directory, os.path.basename(path))
    _prune(directory)
    return path


def _refresh_latest(directory: str, newest: str) -> None:
    """Point `latest` at `newest`, by symlink where that is allowed and by copy where it
    is not.

    Creating a symlink on Windows needs Developer Mode or SeCreateSymbolicLinkPrivilege,
    which the units' service account does not have -- so the copy fallback is the normal
    path there, not an edge case. The fleet's own `mast_mongo_monitor.py` does the same;
    the difference here is that the fallback catches `OSError`/`NotImplementedError`
    rather than everything, per this repo's "narrow the blind excepts" rule.

    `latest` is for a person reading the directory. Nothing in the code follows it.
    """
    link = os.path.join(directory, LATEST)
    try:
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(newest, link)
    except (OSError, NotImplementedError):
        try:
            with open(os.path.join(directory, newest), "rb") as src, open(link, "wb") as dst:
                dst.write(src.read())
        except OSError as ex:
            logger.warning(f"config cache: could not refresh '{link}': {ex}")


def _prune(directory: str, keep: int = KEEP_COPIES) -> None:
    """Delete all but the newest `keep` copies. Housekeeping, never fatal."""
    files = cache_files(directory)
    for name in files[: max(0, len(files) - keep)]:
        try:
            os.unlink(os.path.join(directory, name))
        except OSError as ex:
            logger.warning(f"config cache: could not prune '{name}': {ex}")


def load(
    directory: str, *, mongo_uri: str, database: str, required: Iterable[str] = ()
) -> tuple[dict[str, list[dict]], datetime] | None:
    """The newest usable cache copy and when it was written, or None.

    Tries copies newest-first, so one corrupt file costs a little staleness rather than
    the whole cache. Never raises.

    There is deliberately **no maximum age**. A three-week-old cache that lets a unit
    close its covers beats a fatal startup, and an operator can see the age in the
    degraded reason and in ConfigHealth. Staleness is a fact to report, not a reason to
    refuse.
    """
    for name in reversed(cache_files(directory)):
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError) as ex:
            logger.warning(f"config cache: ignoring unreadable '{name}': {ex}")
            continue

        problem = _why_unusable(payload, mongo_uri=mongo_uri, database=database, required=required)
        if problem:
            logger.warning(f"config cache: ignoring '{name}': {problem}")
            continue

        try:
            written_at = datetime.fromisoformat(payload["written_at"])
        except (KeyError, TypeError, ValueError):
            written_at = datetime.fromtimestamp(os.path.getmtime(path), UTC)

        logger.info(f"config cache: using '{name}', written {written_at.isoformat()}")
        return payload["collections"], written_at

    return None


def _why_unusable(payload: Any, *, mongo_uri: str, database: str, required: Iterable[str] = ()) -> str | None:
    """A human-readable reason to reject this payload, or None to accept it."""
    if not isinstance(payload, dict):
        return f"expected an object, got {type(payload).__name__}"
    if payload.get("schema_version") != SCHEMA_VERSION:
        return f"schema_version {payload.get('schema_version')!r}, expected {SCHEMA_VERSION}"
    # A cache carried over from another deployment would otherwise boot this machine on
    # another site's configuration -- quietly, and with `_validate_local_identity` the
    # only thing standing between that and a telescope.
    if payload.get("database") != database:
        return f"written against database {payload.get('database')!r}, not {database!r}"
    if payload.get("mongo_uri") != mongo_uri:
        return f"written against {payload.get('mongo_uri')!r}, not {mongo_uri!r}"
    collections = payload.get("collections")
    if not isinstance(collections, dict):
        return "no 'collections' object"
    # A partial set is worse than none: startup would succeed and then fail on whichever
    # accessor happened to want the collection that was not there.
    missing = sorted(set(required) - set(collections))
    if missing:
        return f"missing collection(s): {', '.join(missing)}"
    return None
