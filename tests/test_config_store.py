"""The configuration store: copies out, generation-keyed memoization, no write-back.

These are the invariants that phase 1's refresh depends on. The watcher decides whether
anything changed by comparing a fresh read against the stored documents, so a caller that
can edit the store makes it diverge from the database permanently and every refresh
republishes for ever. Two accessors used to do exactly that -- `get_specs` wrote its
merged deepspec bands back, and `get_users` injected an empty `capabilities` list -- which
is why the copy-out tests below are load-bearing rather than tidiness.

No MongoDB: the whole suite is built never to reach it. `Config._reset_for_tests` installs
documents directly, and the instance is built with `object.__new__` so `__init__` (which
does connect) never runs -- the same dodge `unit/tests` uses for the same reason.
"""

import warnings
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from common.config import Config, clear_mongo_ttl_cache
from common.config._memo import by_generation, make_memo, memo_deps
from common.config._snapshot import ConfigSnapshot
from common.config.local import ConfigError

SITES = [
    {
        "name": "ns",
        "project": "mast",
        "controller_host": "mast-ns-control",
        "spec_host": "mast-ns-spec",
        "unit_ids": ["01", "02"],
    }
]
GROUPS = [
    {"name": "everybody", "capabilities": ["canView"]},
    {"name": "admin", "capabilities": ["canChangeUsers", "canUseControls"]},
    {"name": "planners", "capabilities": ["canManagePlans"]},
]
USERS = [
    {"name": "arie", "groups": ["admin"]},
    {"name": "guest", "groups": []},
]
SERVICES = [{"name": "control", "port": 8002}, {"name": "unit"}]


def collections() -> dict[str, list[dict]]:
    """A fresh, independent copy, so one test cannot contaminate another."""
    import copy

    return copy.deepcopy(
        {"sites": SITES, "groups": GROUPS, "users": USERS, "services": SERVICES, "specs": [{"anything": 1}]}
    )


@pytest.fixture
def config():
    Config._reset_for_tests(collections())
    yield object.__new__(Config)
    Config._reset_for_tests()


def bump(name: str, docs: list[dict]) -> None:
    """Publish `docs` as `name`, as phase 1's `_publish` will. Test-local on purpose."""
    old = Config._snapshot
    assert old is not None
    Config._snapshot = replace(
        old,
        collections={**old.collections, name: docs},
        generations={**old.generations, name: old.generations[name] + 1},
        generation=old.generation + 1,
    )


# --------------------------- copies out, never the store ---------------------------


def test_section_hands_out_a_copy(config):
    section = config._section("users")
    section[0]["name"] = "TAMPERED"
    section.append({"name": "injected", "groups": []})

    assert config.snapshot.collections["users"][0]["name"] == "arie"
    assert len(config.snapshot.collections["users"]) == 2


def test_section_copies_deeply(config):
    """A nested edit must not reach the store either -- a shallow copy would let it."""
    config._section("groups")[1]["capabilities"].append("canDoAnything")

    stored = config.snapshot.collections["groups"][1]["capabilities"]
    assert "canDoAnything" not in stored


def test_unknown_collection_raises_configerror_naming_it(config):
    """Was a bare `assert`, which says nothing and vanishes under -O."""
    with pytest.raises(ConfigError) as excinfo:
        config._section("nonesuch")

    assert "nonesuch" in str(excinfo.value)
    assert "users" in str(excinfo.value)  # lists what IS loaded


def test_get_users_does_not_write_capabilities_into_the_store(config):
    """The regression that made the store diverge from the database on every call."""
    config.get_users()

    assert all("capabilities" not in doc for doc in config.snapshot.collections["users"])


def test_accessors_leave_the_store_byte_identical(config):
    import copy

    before = copy.deepcopy(config.snapshot.collections)

    config.get_users()
    config.get_sites()
    config.get_services()

    assert config.snapshot.collections == before


# --------------------------- capability derivation ---------------------------


def test_capabilities_come_from_group_membership(config):
    arie = next(u for u in config.get_users() if u.name == "arie")

    # 'everybody' is added implicitly, so canView arrives without being granted directly.
    assert set(arie.groups) == {"admin", "everybody"}
    assert [str(c) for c in arie.capabilities] == ["canChangeUsers", "canUseControls", "canView"]


def test_can_manage_plans_is_a_known_capability(config):
    """The 'planners' group carries it in the live DB; the enum used to lack the member,
    so every GroupConfig over that document raised and took get_users() down with it."""
    from common.config.identification import GroupConfig

    assert GroupConfig(name="planners", capabilities=["canManagePlans"]).capabilities is not None


def test_unknown_group_is_ignored_not_fatal(config):
    bump("users", [{"name": "arie", "groups": ["no-such-group"]}])

    arie = config.get_users()[0]
    assert [str(c) for c in arie.capabilities] == ["canView"]  # from 'everybody' only


# --------------------------- generation-keyed memoization ---------------------------


def test_same_object_within_a_generation(config):
    """What "an operation uses the configuration it started with" rests on: binding
    `conf = ...` once at entry yields a stable view for the operation's whole duration."""
    assert config.get_users() is config.get_users()
    assert config.get_sites() is config.get_sites()


def test_changing_a_collection_rebuilds_its_dependents(config):
    before = config.get_users()
    bump("users", [{"name": "arie", "groups": ["admin"]}, {"name": "newcomer", "groups": []}])

    after = config.get_users()
    assert after is not before
    assert [u.name for u in after] == ["arie", "newcomer"]


def test_changing_an_unrelated_collection_does_not_rebuild(config):
    """A `users` edit must not invalidate a cached `get_sites`, or per-collection reload
    would buy nothing."""
    before = config.get_sites()
    bump("users", [{"name": "solo", "groups": []}])

    assert config.get_sites() is before


def test_a_declared_dependency_invalidates_too(config):
    """get_users reads `groups` as well as `users`; under-declaring that would serve
    stale capabilities after a group edit."""
    before = config.get_users()
    bump("groups", [{"name": "everybody", "capabilities": ["canView", "canUseControls"]}])

    assert config.get_users() is not before


@pytest.mark.parametrize(
    "accessor,expected",
    [
        ("get_sites", ("sites",)),
        ("get_users", ("users", "groups")),
        ("get_specs", ("specs",)),
        ("get_services", ("services",)),
        ("get_unit", ("units", "sites")),
        ("get_thar_filters", ("specs",)),
    ],
)
def test_accessors_declare_every_collection_they_read(accessor, expected):
    """Under-declaring is the one way to get a stale value out of the memo, and it is
    invisible until a specific collection changes -- so pin the declarations."""
    assert memo_deps(getattr(Config, accessor)) == expected


def test_positional_and_keyword_calls_share_one_entry():
    """Otherwise the "same object within a generation" property would hold only per
    spelling, and two callers of the same accessor could hold different objects."""

    class Toy:
        _memo, _memo_lock = make_memo()
        snapshot = ConfigSnapshot.initial({"units": []}, loaded_at=datetime.now(UTC))

        @by_generation("units")
        def build(self, a=None, b=None, *, _snapshot=None):
            return object()

    toy = Toy()
    assert toy.build("x", "y") is toy.build(b="y", a="x")
    assert toy.build("x", "y") is not toy.build("x", "z")


def test_memo_is_dropped_on_reset(config):
    before = config.get_sites()
    Config._reset_for_tests(collections())

    assert object.__new__(Config).get_sites() is not before


# --------------------------- the retired surface ---------------------------


def test_clear_mongo_ttl_cache_is_a_deprecated_noop():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        clear_mongo_ttl_cache()

    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_fetch_config_section_warns_but_still_copies(config):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        section = config.fetch_config_section("users")

    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    section[0]["name"] = "TAMPERED"
    assert config.snapshot.collections["users"][0]["name"] == "arie"
