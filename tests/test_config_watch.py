"""Refresh, change notification, degraded mode and the boot cache.

All offline. `FakeConfigSource` stands in for MongoDB, which is what makes the
interesting cases testable at all: the behaviour most worth pinning down here is what
happens when the database misbehaves, and that is awkward to arrange against a real
server and trivial against a fake.
"""

import json
import os
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from common.config import Config, ConfigChange
from common.config import _cache as cache_mod
from common.config._watcher import ConfigWatcher
from common.config.local import ConfigError

SITES = [
    {
        "name": "ns",
        "project": "mast",
        "controller_host": "mast-ns-control",
        "spec_host": "mast-ns-spec",
        "unit_ids": ["01"],
    }
]
BASE = {
    "sites": SITES,
    "groups": [{"name": "everybody", "capabilities": ["canView"]}],
    "users": [{"name": "arie", "groups": []}],
    "services": [{"name": "control", "port": 8002}],
    "specs": [{"marker": 1}],
    "units": [{"name": "common"}],
}
URI = "mongodb://mast-ns-control.weizmann.ac.il:27017"
DB = "mast"


class FakeConfigSource:
    """A configuration store under the test's control."""

    def __init__(self, collections=None, *, watchable=True):
        self.collections = {k: [dict(d) for d in v] for k, v in (collections or BASE).items()}
        self.watchable = watchable
        self.fail_with: Exception | None = None
        self.reads = 0
        self.events: list[dict] = []
        self.written: list[tuple[str, dict]] = []

    # -- ConfigSource --
    def read_collection(self, name):
        if self.fail_with:
            raise self.fail_with
        self.reads += 1
        return [dict(d) for d in self.collections[name]]

    def read_all(self, names):
        return {name: self.read_collection(name) for name in names}

    def supports_watch(self):
        return self.watchable

    def watch(self, collections, *, max_await_time_ms):
        if self.fail_with:
            raise self.fail_with
        return _FakeStream(self)

    def write_unit_delta(self, unit_name, delta):
        if self.fail_with:
            raise self.fail_with
        self.written.append((unit_name, delta))

    def close(self):
        pass

    # -- test controls --
    def set(self, name, docs):
        """Change a collection and queue the event a change stream would deliver."""
        self.collections[name] = [dict(d) for d in docs]
        self.events.append({"operationType": "update", "ns": {"coll": name}})


class _FakeStream:
    def __init__(self, source):
        self._source = source

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def try_next(self):
        return self._source.events.pop(0) if self._source.events else None


class _FakeLocal:
    """Just the `LocalConfig` fields the refresh path reads."""

    mongo_uri = URI
    database = DB
    site = "ns"

    def __init__(self, config_cache_dir: str):
        self.config_cache_dir = config_cache_dir


def make_config(source=None, *, collections=None, cache_dir=None):
    """A Config wired to `source`, with no MongoDB and no __init__."""
    Config._reset_for_tests(collections or {k: [dict(d) for d in v] for k, v in BASE.items()})
    cfg = object.__new__(Config)
    cfg.source = source or FakeConfigSource()
    # A real directory even when a test does not care, so cache writes on the refresh
    # path exercise the real code instead of its "could not write" branch.
    cfg.local = _FakeLocal(cache_dir or tempfile.mkdtemp(prefix="mast-config-cache-"))
    return cfg


@pytest.fixture(autouse=True)
def _clean():
    yield
    Config._reset_for_tests()


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --------------------------- publishing and change detection ---------------------------


def test_publish_bumps_only_what_changed():
    cfg = make_config()
    before = cfg.snapshot

    changed = cfg._publish({"users": [{"name": "arie", "groups": []}, {"name": "new", "groups": []}]})

    assert changed == {"users"}
    assert cfg.snapshot.generations["users"] == before.generations["users"] + 1
    assert cfg.snapshot.generations["sites"] == before.generations["sites"]
    assert cfg.snapshot.generation == before.generation + 1


def test_publishing_identical_documents_changes_nothing():
    cfg = make_config()
    before = cfg.snapshot

    assert cfg._publish({"users": [dict(d) for d in before.collections["users"]]}) == frozenset()
    assert cfg.snapshot.generation == before.generation


def test_in_place_edit_of_an_existing_document_is_detected():
    """The case a (count, max _id) fingerprint cannot see -- and the dominant one here,
    since it is what an operator editing a value produces."""
    cfg = make_config()

    changed = cfg._publish({"services": [{"name": "control", "port": 9999}]})

    assert changed == {"services"}


def test_reload_reads_only_the_named_collections():
    source = FakeConfigSource()
    cfg = make_config(source)
    source.reads = 0

    cfg._reload(["users"])

    assert source.reads == 1


def test_reload_ignores_unknown_collection_names():
    source = FakeConfigSource()
    cfg = make_config(source)
    source.reads = 0

    cfg._reload(["not-a-collection"])

    assert source.reads == 0


# --------------------------- on_change ---------------------------


def test_callback_receives_only_what_changed():
    cfg = make_config()
    seen: list[ConfigChange] = []
    cfg.on_change(seen.append, name="test")

    cfg.source.set("users", [{"name": "solo", "groups": []}])
    cfg._reload(["users"])

    assert wait_for(lambda: seen)
    assert seen[-1].collections == {"users"}
    assert seen[-1].degraded is False


def test_a_filtered_callback_is_not_woken_by_another_collection():
    cfg = make_config()
    woken = threading.Event()
    cfg.on_change(lambda _: woken.set(), collections=("units",), name="units-only")

    cfg.source.set("users", [{"name": "solo", "groups": []}])
    cfg._reload(["users"])

    assert not wait_for(woken.is_set, timeout=0.5)


def test_changes_during_a_slow_callback_coalesce_into_one():
    """Three publishes while a callback is busy must deliver once, with the union --
    not three times, and not with a queue that can grow."""
    cfg = make_config()
    started = threading.Event()
    release = threading.Event()
    calls: list[ConfigChange] = []

    def slow(change):
        calls.append(change)
        started.set()
        release.wait(timeout=3)

    cfg.on_change(slow, name="slow")

    cfg._publish({"users": [{"name": "a", "groups": []}]})
    cfg._enqueue_change(ConfigChange(generation=1, collections=frozenset({"users"}), degraded=False))
    assert started.wait(timeout=3)

    for name, docs in (("sites", []), ("services", []), ("specs", [])):
        cfg._publish({name: docs})
        cfg._enqueue_change(ConfigChange(generation=9, collections=frozenset({name}), degraded=False))
    release.set()

    assert wait_for(lambda: len(calls) >= 2)
    assert calls[1].collections == {"sites", "services", "specs"}
    assert len(calls) == 2


def test_a_raising_callback_does_not_silence_the_others():
    cfg = make_config()
    survived: list[ConfigChange] = []

    def bad(_change):
        raise RuntimeError("callback is broken")

    cfg.on_change(bad, name="bad")
    cfg.on_change(survived.append, name="good")

    cfg.source.set("users", [{"name": "solo", "groups": []}])
    cfg._reload(["users"])

    assert wait_for(lambda: survived)


def test_a_failing_callback_is_not_unregistered():
    """Silently disabling an operator-visible behaviour is worse than a noisy log."""
    cfg = make_config()
    calls = []

    def bad(change):
        calls.append(change)
        raise RuntimeError("still broken")

    cfg.on_change(bad, name="bad")

    for i in range(3):
        cfg.source.set("users", [{"name": f"u{i}", "groups": []}])
        cfg._reload(["users"])
        assert wait_for(lambda n=i: len(calls) >= n + 1)

    assert len(calls) == 3


def test_unsubscribe_stops_delivery():
    cfg = make_config()
    seen = []
    unsubscribe = cfg.on_change(seen.append, name="temp")
    unsubscribe()

    cfg.source.set("users", [{"name": "solo", "groups": []}])
    cfg._reload(["users"])

    assert not wait_for(lambda: seen, timeout=0.5)


# --------------------------- degraded and recovery ---------------------------


def test_degraded_is_flagged_and_reported():
    cfg = make_config()
    cfg._set_degraded("database unreachable")

    assert cfg.degraded is True
    assert "unreachable" in cfg.degraded_reason
    assert cfg.health.degraded is True


def test_documents_stay_usable_while_degraded():
    cfg = make_config()
    cfg._set_degraded("database unreachable")

    assert [s.name for s in cfg.get_sites()] == ["ns"]


def test_recovery_reports_what_changed_while_blind():
    cfg = make_config()
    seen: list[ConfigChange] = []
    cfg.on_change(seen.append, name="test")

    cfg._set_degraded("database unreachable")
    cfg.source.set("users", [{"name": "changed-while-down", "groups": []}])
    cfg._reload_all()

    assert cfg.degraded is False
    assert wait_for(lambda: any(c.reason == "recovered" for c in seen))
    assert seen[-1].collections == {"users"}


def test_recovery_notifies_even_with_nothing_changed():
    cfg = make_config()
    seen: list[ConfigChange] = []
    cfg.on_change(seen.append, name="test")

    cfg._set_degraded("database unreachable")
    cfg._reload_all()

    assert cfg.degraded is False
    assert wait_for(lambda: any(c.reason == "recovered" for c in seen))


def test_set_unit_refuses_while_degraded():
    cfg = make_config()
    cfg._set_degraded("database unreachable")

    with pytest.raises(ConfigError, match="degraded"):
        cfg.set_unit(unit_name="mast01", unit_conf=object())


# --------------------------- the watcher loop ---------------------------


def make_watcher(source, **kwargs):
    reloaded: list[tuple] = []
    watcher = ConfigWatcher(
        source,
        list(BASE),
        reload_all=lambda: reloaded.append(("all",)),
        reload=lambda names: reloaded.append(("some", frozenset(names))),
        on_degraded=lambda reason: None,
        on_healthy=lambda: None,
        **kwargs,
    )
    return watcher, reloaded


def test_watcher_reads_everything_before_it_starts_watching():
    """After any gap -- startup, a dropped stream, an outage -- a full read is what makes
    "events are triggers" safe."""
    source = FakeConfigSource()
    watcher, reloaded = make_watcher(source)
    watcher.start()
    try:
        assert wait_for(lambda: ("all",) in reloaded)
    finally:
        watcher.stop()


def test_watcher_reloads_the_collection_an_event_names():
    source = FakeConfigSource()
    watcher, reloaded = make_watcher(source)
    watcher.start()
    try:
        assert wait_for(lambda: ("all",) in reloaded)
        source.set("units", [{"name": "common", "changed": True}])
        assert wait_for(lambda: ("some", frozenset({"units"})) in reloaded)
    finally:
        watcher.stop()


def test_watcher_reloads_each_collection_in_a_burst():
    """One event, one reload. Coalescing deliberately lives on the notification side --
    the watcher used to collect bursts by calling try_next() again, which blocks for the
    whole idle timeout when no further event is waiting, and so delayed *every* change by
    WATCH_AWAIT_MS (measured: ~10s against rs0 for a change the server delivered at once)."""
    source = FakeConfigSource()
    watcher, reloaded = make_watcher(source)
    watcher.start()
    try:
        assert wait_for(lambda: ("all",) in reloaded)
        source.set("units", [{"name": "common"}])
        source.set("users", [{"name": "x", "groups": []}])
        source.set("sites", SITES)

        assert wait_for(
            lambda: (
                {n for kind, n in (r for r in reloaded if r[0] == "some")}
                >= {
                    frozenset({"units"}),
                    frozenset({"users"}),
                    frozenset({"sites"}),
                }
            )
        )
    finally:
        watcher.stop()


def test_a_burst_reaches_a_callback_once():
    """What the burst handling is actually for: three collections changing in quick
    succession wake a callback once, with the union."""
    cfg = make_config()
    seen: list[ConfigChange] = []
    blocked = threading.Event()

    def slow(change):
        seen.append(change)
        blocked.wait(timeout=2) if len(seen) == 1 else None

    cfg.on_change(slow, name="slow")

    for name, docs in (("users", [{"name": "u", "groups": []}]), ("services", []), ("specs", [])):
        cfg.source.set(name, docs)
        cfg._reload([name])
    blocked.set()

    assert wait_for(lambda: len(seen) >= 2)
    assert set().union(*(c.collections for c in seen)) == {"users", "services", "specs"}


def test_watcher_polls_when_change_streams_are_unavailable():
    """A developer's standalone mongod refuses change streams; the poll path must take
    over rather than the process failing."""
    source = FakeConfigSource(watchable=False)
    watcher, reloaded = make_watcher(source, poll_seconds=0.05)
    watcher.start()
    try:
        assert wait_for(lambda: reloaded.count(("all",)) >= 2)
    finally:
        watcher.stop()


def test_watcher_survives_a_failing_source_and_recovers():
    source = FakeConfigSource()
    source.fail_with = ConfigError("database unreachable")
    degraded: list[str] = []
    watcher = ConfigWatcher(
        source,
        list(BASE),
        reload_all=lambda: source.read_all(BASE),
        reload=lambda names: None,
        on_degraded=degraded.append,
        on_healthy=lambda: None,
    )
    watcher.start()
    try:
        assert wait_for(lambda: degraded)
        assert watcher.running  # still alive despite the failure
        source.fail_with = None
        assert wait_for(lambda: source.reads > 0, timeout=5)
    finally:
        watcher.stop()


def test_watcher_start_is_idempotent():
    source = FakeConfigSource()
    watcher, _ = make_watcher(source)
    watcher.start()
    try:
        first = watcher._thread
        watcher.start()
        assert watcher._thread is first
    finally:
        watcher.stop()


def test_start_watching_honours_the_opt_out(monkeypatch):
    monkeypatch.setenv("MAST_CONFIG_NO_WATCH", "1")
    cfg = make_config()

    cfg.start_watching()

    assert Config._watcher is None


# --------------------------- the boot cache ---------------------------


def write_cache(tmp_path, collections=None, when=None, **overrides):
    return cache_mod.write(
        str(tmp_path),
        collections or BASE,
        mongo_uri=overrides.get("mongo_uri", URI),
        database=overrides.get("database", DB),
        generation=overrides.get("generation", 0),
        written_by="test-host",
        when=when,
    )


def test_cache_round_trips(tmp_path):
    write_cache(tmp_path)

    loaded = cache_mod.load(str(tmp_path), mongo_uri=URI, database=DB, required=BASE)

    assert loaded is not None
    collections, written_at = loaded
    assert collections["sites"][0]["name"] == "ns"
    assert written_at.tzinfo is not None


def test_latest_points_at_the_newest(tmp_path):
    write_cache(tmp_path, when=datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    newest = write_cache(tmp_path, when=datetime(2026, 8, 31, 12, 0, tzinfo=UTC))

    latest = os.path.join(str(tmp_path), cache_mod.LATEST)
    if os.path.islink(latest):
        assert os.readlink(latest) == os.path.basename(newest)
    else:
        with open(latest) as fp:
            assert json.load(fp)["written_at"].startswith("2026-08-31")


def test_latest_falls_back_to_a_copy_when_symlinks_are_refused(tmp_path, monkeypatch):
    """The Windows path -- symlinks need a privilege the service account lacks. CI's
    Linux half would never exercise it otherwise."""

    def refuse(*_a, **_kw):
        raise OSError("symlinks not permitted")

    monkeypatch.setattr(os, "symlink", refuse)
    write_cache(tmp_path)

    latest = os.path.join(str(tmp_path), cache_mod.LATEST)
    assert os.path.isfile(latest) and not os.path.islink(latest)
    assert cache_mod.load(str(tmp_path), mongo_uri=URI, database=DB) is not None


def test_only_ten_copies_are_kept(tmp_path):
    for minute in range(12):
        write_cache(tmp_path, when=datetime(2026, 8, 31, 12, minute, tzinfo=UTC))

    files = cache_mod.cache_files(str(tmp_path))
    assert len(files) == 10
    # The two dropped are the oldest, by the same name sort used to find the newest.
    assert files[0] == f"{cache_mod.PREFIX}20260831T120200Z{cache_mod.SUFFIX}"


def test_a_partial_write_is_neither_loaded_nor_pruned(tmp_path):
    write_cache(tmp_path)
    stray = tmp_path / f"{cache_mod.PREFIX}20260831T999999Z{cache_mod.SUFFIX}.tmp"
    stray.write_text("{ truncated")

    assert stray.name not in cache_mod.cache_files(str(tmp_path))
    assert cache_mod.load(str(tmp_path), mongo_uri=URI, database=DB) is not None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"mongo_uri": "mongodb://elsewhere:27017"}, id="another-deployment"),
        pytest.param({"database": "other-db"}, id="another-database"),
    ],
)
def test_a_cache_from_another_deployment_is_refused(tmp_path, overrides):
    """Otherwise a cache carried between machines would quietly boot this one on another
    site's configuration, with _validate_local_identity the only thing in the way."""
    write_cache(tmp_path, **overrides)

    assert cache_mod.load(str(tmp_path), mongo_uri=URI, database=DB) is None


def test_a_cache_missing_a_collection_is_refused(tmp_path):
    """A partial set is worse than none: startup would succeed and then fail on whichever
    accessor wanted the collection that was not there."""
    write_cache(tmp_path, collections={k: v for k, v in BASE.items() if k != "units"})

    assert cache_mod.load(str(tmp_path), mongo_uri=URI, database=DB, required=BASE) is None


def test_a_corrupt_copy_is_skipped_for_an_older_good_one(tmp_path):
    """One bad file costs a little staleness, not the whole cache."""
    write_cache(tmp_path, collections={**BASE, "sites": SITES}, when=datetime(2026, 8, 30, 12, 0, tzinfo=UTC))
    newest = write_cache(tmp_path, when=datetime(2026, 8, 31, 12, 0, tzinfo=UTC))
    with open(newest, "w") as fp:
        fp.write("{ not json")

    loaded = cache_mod.load(str(tmp_path), mongo_uri=URI, database=DB)

    assert loaded is not None
    collections, written_at = loaded
    assert written_at.date() == datetime(2026, 8, 30, tzinfo=UTC).date()
    assert collections["sites"][0]["name"] == "ns"


def test_no_maximum_age(tmp_path):
    """A three-week-old cache that lets a unit close its covers beats a fatal startup."""
    write_cache(tmp_path, when=datetime.now(UTC) - timedelta(days=21))

    assert cache_mod.load(str(tmp_path), mongo_uri=URI, database=DB) is not None


def test_an_unwritable_directory_is_not_fatal(tmp_path):
    target = tmp_path / "file-in-the-way"
    target.write_text("not a directory")

    assert write_cache(target) is None


def test_load_from_a_directory_that_does_not_exist(tmp_path):
    assert cache_mod.load(str(tmp_path / "nope"), mongo_uri=URI, database=DB) is None
