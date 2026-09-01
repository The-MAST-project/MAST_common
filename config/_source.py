"""Where configuration documents come from.

The seam exists so that everything above it -- publishing, change detection, coalescing,
backoff, the degraded transitions, the boot cache -- is testable with no MongoDB. That
matters here more than it usually would: this repo's suite is built never to reach Mongo
(see `tests/conftest.py`), there is no mongomock, and the behaviour most worth testing is
precisely what happens when the database misbehaves, which is awkward to arrange against
a real server and trivial against a fake.

`MongoConfigSource` is the only production implementation and is deliberately thin: it
does the pymongo calls and nothing else, so there is little in it that a fake can get
wrong by omission.
"""

from collections.abc import Iterable
from contextlib import AbstractContextManager
from typing import Any, Protocol

import pymongo.database
from pymongo.errors import OperationFailure, PyMongoError

from common.mast_logging import get_logger

from .local import ConfigError

logger = get_logger(__name__)


class ConfigSource(Protocol):
    """The operations `Config` and its watcher need from a configuration store."""

    def read_collection(self, name: str) -> list[dict[str, Any]]: ...

    def read_all(self, names: Iterable[str]) -> dict[str, list[dict[str, Any]]]: ...

    def supports_watch(self) -> bool: ...

    def watch(self, collections: Iterable[str], *, max_await_time_ms: int) -> AbstractContextManager: ...

    def write_unit_delta(self, unit_name: str, delta: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class MongoConfigSource:
    """The configuration database, over pymongo.

    Every method that touches the network raises `ConfigError` and nothing else.
    `ConfigError` is the only failure type this area's callers are written against, and
    keeping the translation here means the policy -- die at startup, degrade in the
    watcher -- belongs to the caller rather than to the driver (MAST_common#82).
    """

    def __init__(self, database_factory, mongo_uri: str, database_name: str):
        # A factory rather than a Database, so the client is still created lazily and
        # still owned by ConfigOrigin. This class is a translator, not a connection owner.
        self._database_factory = database_factory
        self.mongo_uri = mongo_uri
        self.database_name = database_name
        self._watch_supported: bool | None = None

    def _db(self) -> pymongo.database.Database:
        return self._database_factory()

    def read_collection(self, name: str) -> list[dict[str, Any]]:
        try:
            # `_id` is projected out: nothing downstream uses it, it is not JSON-safe for
            # the boot cache, and leaving it out keeps documents comparable by value --
            # which is how change detection decides whether anything actually changed.
            return list(self._db()[name].find({}, projection={"_id": False}))
        except PyMongoError as ex:
            raise ConfigError(
                f"cannot read collection '{name}' from database '{self.database_name}' "
                f"at {self.mongo_uri}: {type(ex).__name__}: {ex}"
            ) from ex

    def read_all(self, names: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
        return {name: self.read_collection(name) for name in names}

    def supports_watch(self) -> bool:
        """Whether change streams are available, decided once by trying one.

        Change streams need a replica set or a mongos; a standalone `mongod` refuses with
        `OperationFailure`. The fleet's database is a single-member replica set (`rs0`),
        so this is normally True -- but a developer's standalone Mongo is not, and there
        the poll path must take over rather than the process failing.
        """
        if self._watch_supported is None:
            try:
                with self._db().watch(max_await_time_ms=1):
                    pass
                self._watch_supported = True
            except OperationFailure as ex:
                logger.info(f"config: change streams unavailable ({ex}); falling back to polling")
                self._watch_supported = False
            except PyMongoError as ex:
                # Unreachable rather than unsupported -- do not remember that as "no
                # change streams" for the life of the process.
                logger.debug(f"config: could not probe change-stream support: {ex}")
                return False
        return self._watch_supported

    def watch(self, collections: Iterable[str], *, max_await_time_ms: int) -> AbstractContextManager:
        """A change stream over `collections`.

        No `full_document`: an event here is a trigger, never data. A delete carries only
        `documentKey._id`, and `_id` is projected out of everything stored, so an event
        cannot be mapped back to an in-memory document however much of it we ask for.
        Every event therefore causes a full re-read of the collection it names, which
        makes `full_document` pure wire cost.
        """
        pipeline = [{"$match": {"ns.coll": {"$in": list(collections)}}}]
        try:
            return self._db().watch(pipeline=pipeline, max_await_time_ms=max_await_time_ms)
        except PyMongoError as ex:
            raise ConfigError(f"cannot open a change stream on '{self.database_name}': {type(ex).__name__}: {ex}") from ex

    def write_unit_delta(self, unit_name: str, delta: dict[str, Any]) -> None:
        try:
            self._db()["units"].update_one({"name": unit_name}, {"$set": delta}, upsert=True)
        except PyMongoError as ex:
            raise ConfigError(
                f"cannot save the configuration for unit '{unit_name}' to '{self.database_name}' "
                f"at {self.mongo_uri}: {type(ex).__name__}: {ex}"
            ) from ex

    def close(self) -> None:
        # The client belongs to ConfigOrigin, which closes it.
        return None
