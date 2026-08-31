import json
import socket
import threading
import warnings
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, ClassVar

import pymongo
import pymongo.database
from cachetools import TTLCache, cached
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from common.deep import deep_dict_difference, deep_dict_is_empty, deep_dict_update
from common.mast_logging import get_logger
from common.utils import function_name

from ._memo import by_generation, make_memo
from ._snapshot import ConfigSnapshot
from .identification import GroupConfig, UserConfig
from .local import ConfigError, LocalConfig, load_local_config
from .site import Site
from .unit import UnitConfig
from .vault import VaultConfig, load_vault

# The collections that make up the MAST configuration database. This is the DB
# schema/layout (not a per-deployment setting), so it stays a module constant.
DEFAULT_COLLECTIONS = ("groups", "services", "sites", "specs", "units", "users")

#: Fail fast rather than at pymongo's 30 s default. A unit whose controller is down
#: would otherwise block half a minute at startup discovering that, and every later
#: retry would cost the same again.
SERVER_SELECTION_TIMEOUT_MS = 5_000
CONNECT_TIMEOUT_MS = 5_000

#: A power switch's address does not change hourly, and `get_unit` resolves one. This
#: is the only remaining time-keyed cache in the module, and it caches DNS, not
#: configuration -- the distinction that makes a TTL the right tool here and the wrong
#: one for the configuration itself (see `_memo.py`).
DNS_CACHE_TTL_SECONDS = 3600

logger = get_logger(__name__)


class ServiceConfig(BaseModel):
    name: str
    listen_on: str = "0.0.0.0"
    port: int = 8000


@cached(TTLCache(maxsize=64, ttl=DNS_CACHE_TTL_SECONDS), lock=threading.Lock())
def _resolve_host(hostname: str) -> str | None:
    """The IP for `hostname`, or None if it does not resolve. Never raises."""
    try:
        return socket.gethostbyname(hostname)
    except socket.gaierror:
        logger.warning(f"could not resolve {hostname=}")
        return None


def clear_mongo_ttl_cache() -> None:
    """Deprecated no-op, kept only so an out-of-tree caller does not break.

    It used to clear a `TTLCache` wrapping a loader that nothing called after startup,
    so clearing it never caused a re-read -- which is why `set_unit` calling this did
    not make a process see its own write. The caches it managed are gone; accessor
    results are now keyed on the configuration's generation instead (`_memo.py`).
    """
    warnings.warn(
        "clear_mongo_ttl_cache() is a no-op and will be removed; configuration caching "
        "is keyed on the config generation, not on time.",
        DeprecationWarning,
        stacklevel=2,
    )


def _make_client(mongo_uri: str, machine_role: str) -> MongoClient:
    """The one MongoClient this process uses for configuration.

    `directConnection=True` is load-bearing, not a tuning knob. The config DB is a
    single-member replica set (`rs0`), and that member advertises itself under the bare
    hostname `mast-ns-control:27017`, which does not resolve off the controller's own
    subnet -- the exact failure DECISIONS [2026-07-09] fixed by composing the FQDN. With
    replica-set discovery enabled the driver would replace our FQDN seed with that
    advertised bare name and every unit would lose the database. A direct connection has
    no discovery step to go wrong, and change streams work over it (verified against
    `rs0`: topology Single, is_primary True, resume token returned).

    Deliberately NOT `socketTimeoutMS`: a change stream's awaitData cursor blocks by
    design, and a socket timeout would tear it down on every quiet interval. That wait is
    bounded by `watch(max_await_time_ms=...)` instead.
    """
    return MongoClient(
        mongo_uri,
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
        connectTimeoutMS=CONNECT_TIMEOUT_MS,
        directConnection=True,
        # Which of the fleet's ~forty processes opened a given cursor is otherwise
        # unanswerable from `db.currentOp()`.
        appname=f"mast-{machine_role}-{socket.gethostname().split('.')[0]}",
    )


class ConfigOrigin:
    _instance = None
    _initialized = False
    _client_lock: ClassVar[threading.Lock] = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        mongo_uri: str | None = None,
        database_name: str | None = None,
        collections: tuple[str, ...] | None = None,
        machine_role: str = "unknown",
    ):
        if self._initialized:
            return

        self.mongo_uri = mongo_uri
        self.database_name = database_name
        self.collections = collections
        self.machine_role = machine_role
        self.query_filter: dict[str, Any] | None = None
        self.client: MongoClient | None = None
        self.db: pymongo.database.Database | None = None

        self._initialized = True

    def database(self) -> pymongo.database.Database:
        """The configuration database, connecting on first use.

        One client for the life of the process, shared by reads, `set_unit`'s write and
        (from phase 1) the change stream. The previous code built a fresh `MongoClient`
        on every cache miss and never closed the old one, so each miss leaked a
        connection pool and its monitor threads.
        """
        if self.db is not None:
            return self.db

        with ConfigOrigin._client_lock:
            if self.db is None:  # re-check: another thread may have connected
                if not (self.mongo_uri and self.database_name):
                    raise ConfigError("missing mongo_uri or database name; cannot reach the configuration database.")
                self.client = _make_client(self.mongo_uri, self.machine_role)
                self.db = self.client[self.database_name]
        return self.db

    def close(self) -> None:
        """Drop the connection. Safe to call more than once."""
        with ConfigOrigin._client_lock:
            if self.client is not None:
                self.client.close()
            self.client = None
            self.db = None


class Config:
    _instance = None
    _initialized: bool = False
    #: Lazily loaded on first access to `vault`; never populated in __init__,
    #: so constructing a Config does not reach for the share.
    _vault: "VaultConfig | None" = None

    #: The published configuration. Replaced wholesale, never mutated in place, so a
    #: reader needs no lock to obtain a self-consistent view (see `_snapshot.py`).
    _snapshot: ClassVar[ConfigSnapshot | None] = None
    #: Serialises publishers only. Readers never take it.
    _publish_lock: ClassVar[threading.Lock] = threading.Lock()

    _memo: ClassVar[Any]
    _memo_lock: ClassVar[threading.Lock]
    _memo, _memo_lock = make_memo()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """
        Loads the MAST configuration database from MongoDB.

        The bootstrap parameters (which site this machine is, and how to reach the
        MongoDB server) come from the local TOML configuration file (see
        `common.config.local`), which is the single source of truth. After loading,
        `_validate_local_identity()` cross-checks the local config against the DB
        'sites' document so the two cannot drift silently.
        """
        if self._initialized:
            return

        self.local: LocalConfig = load_local_config()

        self.origin = ConfigOrigin(
            mongo_uri=self.local.mongo_uri,
            database_name=self.local.database,
            collections=DEFAULT_COLLECTIONS,
            machine_role=self.local.machine_role,
        )
        Config._snapshot = ConfigSnapshot.initial(self._read_all(), loaded_at=datetime.now(UTC))
        self._validate_local_identity()

        self._initialized = True

    # ------------ the published store ------------

    @property
    def snapshot(self) -> ConfigSnapshot:
        """The configuration currently in force."""
        snapshot = Config._snapshot
        if snapshot is None:
            raise ConfigError("the configuration has not been loaded yet.")
        return snapshot

    @property
    def generation(self) -> int:
        """Bumps whenever any collection changes. Per-process; not an identity."""
        return self.snapshot.generation

    @classmethod
    def _reset_for_tests(cls, collections: dict[str, list[dict[str, Any]]] | None = None) -> None:
        """Drop the singleton and optionally install a configuration directly.

        Test-only; there is no production caller. It also addresses a standing problem
        with this class: `Config` is a process-wide singleton guarded by `_initialized`,
        so without a reset the first test to build one leaks it into every test that
        follows for the rest of the session.
        """
        cls._instance = None
        cls._initialized = False
        cls._vault = None
        cls._snapshot = None if collections is None else ConfigSnapshot.initial(collections, datetime.now(UTC))
        cls._memo, cls._memo_lock = make_memo()
        ConfigOrigin._instance = None
        ConfigOrigin._initialized = False

    # ------------ MongoDB backend ------------

    def _read_collection(self, name: str) -> list[dict[str, Any]]:
        """Every document in one collection, with `_id` projected out.

        pymongo's exceptions are wrapped in `ConfigError` here, at the single point they
        can arise, because `ConfigError` is the only failure type this area's callers are
        written against -- `MAST_unit/src/app.py` catches it to fail startup with a
        stated reason. An unreachable controller used to escape as a bare
        `ServerSelectionTimeoutError`, so that handler never ran and the operator got a
        traceback and a service that restarted forever with nothing listening
        (MAST_common#82). Wrapping low leaves the *policy* to the caller: startup lets it
        propagate and dies loudly, the watcher catches it and degrades.
        """
        try:
            cursor = self.origin.database()[name].find(self.origin.query_filter or {}, projection={"_id": False})
            return list(cursor)
        except PyMongoError as ex:
            raise ConfigError(
                f"cannot read collection '{name}' from database '{self.origin.database_name}' "
                f"at {self.origin.mongo_uri}: {type(ex).__name__}: {ex}"
            ) from ex

    def _read_all(self) -> dict[str, list[dict[str, Any]]]:
        """Every configuration collection. Raises `ConfigError` if any read fails."""
        if not (self.origin.mongo_uri and self.origin.database_name and self.origin.collections):
            raise ConfigError("missing mongo_uri, database, or collections; cannot load configuration.")
        return {name: self._read_collection(name) for name in self.origin.collections}

    def _section(self, name: str, snapshot: ConfigSnapshot | None = None) -> list[dict[str, Any]]:
        """A private copy of one collection's documents.

        A copy, not the stored list. The store is compared against a fresh read to decide
        whether anything actually changed, so a caller that edits what it was handed makes
        the store permanently differ from the database and every refresh would republish
        for ever. `get_specs` and `get_users` both used to do exactly that -- see their
        comments. Handing out a copy makes that class of bug impossible rather than
        merely fixed.

        `ConfigError` rather than the previous bare `assert`: asserts vanish under `-O`,
        and an `AssertionError` naming nothing is not a diagnosis.
        """
        snapshot = snapshot or self.snapshot
        docs = snapshot.collections.get(name)
        if docs is None:
            raise ConfigError(f"the configuration has no '{name}' collection (loaded: {sorted(snapshot.collections)}).")
        return deepcopy(docs)

    def fetch_config_section(self, section: str) -> list[dict[str, Any]]:
        """Deprecated alias for the private `_section`.

        It used to return the stored list itself, so callers could (and did) edit the
        shared configuration. Kept briefly because this package is consumed unpinned;
        no in-tree caller remains.
        """
        warnings.warn(
            "fetch_config_section() is private to Config and will be removed; use the get_*() accessors instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self._section(section)

    def _validate_local_identity(self) -> None:
        """Cross-check the local TOML config against the DB 'sites' document.

        `project`, `controller_host` and the geographic location are intentionally
        duplicated in both the config file and the MongoDB 'sites' collection. They
        MUST agree; if they don't, raise `ConfigError` with the exact diff so the
        drift fails the application loudly at startup instead of going unnoticed.
        """
        db_site = next((s for s in self.get_sites() if s.name == self.local.site), None)
        if db_site is None:
            raise ConfigError(
                f"site '{self.local.site}' (from the config file) is not present in "
                f"the 'sites' collection of database '{self.local.database}' on "
                f"{self.local.mongo_uri}."
            )

        mismatches: list[str] = []
        for field in ("project", "controller_host"):
            local_value = getattr(self.local, field)
            db_value = getattr(db_site, field)
            if local_value != db_value:
                mismatches.append(f"  - {field}: config file = {local_value!r}, DB site = {db_value!r}")
        for attr in ("latitude", "longitude", "elevation"):
            local_value = getattr(self.local.location, attr)
            db_value = getattr(db_site.location, attr)
            if local_value != db_value:
                mismatches.append(f"  - location.{attr}: config file = {local_value!r}, DB site = {db_value!r}")
        if mismatches:
            raise ConfigError(
                f"local configuration for site '{self.local.site}' disagrees with the "
                "DB 'sites' document (these must match):\n" + "\n".join(mismatches)
            )

    # ------------ accessors ------------

    def _verify_unit_site_membership(self, site_name: str, unit_name: str, snapshot: ConfigSnapshot | None = None) -> bool:
        unit_name = unit_name.lower()
        sites = self.get_sites(_snapshot=snapshot)
        site = [s for s in sites if s.name == site_name]
        if not site:
            logger.error(f"{function_name()}: no site named '{site_name}'")
            return False
        if unit_name not in site[0].unit_ids:
            logger.error(f"{function_name()}: site '{site_name}' has no unit named '{unit_name}'")
            return False
        return True

    def site_name_from_unit_name(self, unit_name: str, snapshot: ConfigSnapshot | None = None) -> str | None:
        unit_name = unit_name.lower()
        for site in self.get_sites(_snapshot=snapshot):
            if unit_name in site.unit_ids:
                return site.name
        return None

    @by_generation("units", "sites")
    def get_unit(
        self,
        site_name: str | None = None,
        unit_name: str | None = None,
        *,
        _snapshot: ConfigSnapshot | None = None,
    ) -> UnitConfig | None:
        """
        Gets a unit's configuration.  By default, this is the ['config']['units']['common']
         entry. If a unit-specific entry exists it overrides the 'common' entry.

        Note: The current database layout has all the units in a single 'units' collection.
         In the future we may want to separate them by site.  For sanity we lookup the unit name
            within the specified site.
        """

        local_unit = unit_name is None
        if unit_name is None:
            unit_name = socket.gethostname().split(".")[0]
        unit_name = unit_name.lower()

        if site_name is None:
            # For the local machine the site is the config-file site (source of
            # truth); for an explicitly-named unit, look it up by DB membership.
            site_name = self.local.site if local_unit else self.site_name_from_unit_name(unit_name, _snapshot)
            if site_name is None:
                logger.error(f"{function_name()}: cannot determine site for unit '{unit_name}'")
                return None

        if not self._verify_unit_site_membership(site_name, unit_name or "", _snapshot):
            return None

        units = self._section("units", _snapshot)
        if unit_name not in [unit["name"] for unit in units]:
            return None

        common_config = next((unit for unit in units if unit.get("name") == "common"), None)
        if common_config is None:
            raise ValueError("get_unit: 'common' unit configuration not found")

        # None is legitimate here: a unit may have no unit-specific entry in the DB,
        # in which case it is entirely described by 'common'.
        unit_config = next((unit for unit in units if unit.get("name") == unit_name), None)

        combined_dict: dict = deepcopy(common_config)
        if unit_config:
            deep_dict_update(combined_dict, unit_config)

        # resolve power-switch name and ipaddr
        combined_dict["name"] = unit_name
        if combined_dict["power_switch"]["network"]["host"] == "auto":
            switch_host_name = unit_name.replace("mast", "mastps") + "." + self.local.domain
            combined_dict["power_switch"]["network"]["host"] = switch_host_name
            if "ipaddr" not in combined_dict["power_switch"]["network"]:
                ipaddr = _resolve_host(switch_host_name)
                if ipaddr is not None:
                    combined_dict["power_switch"]["network"]["ipaddr"] = ipaddr

        try:
            ret = UnitConfig(**combined_dict)
        except Exception as ex:
            logger.error(f"get_unit: failed to parse unit configuration for {unit_name=}: {ex}")
            raise
        return ret

    def set_unit(
        self,
        site_name: str | None = None,
        unit_name: str | None = None,
        unit_conf: UnitConfig | None = None,
    ):
        if unit_conf is None:
            raise ValueError(f"{function_name()}: unit_conf cannot be None")
        unit_dict = unit_conf.model_dump()

        local_unit = unit_name is None
        if unit_name is None:
            unit_name = socket.gethostname().split(".")[0]
        if site_name is None:
            # Local machine -> config-file site; explicit unit -> DB membership.
            site_name = self.local.site if local_unit else self.site_name_from_unit_name(unit_name)
            if site_name is None:
                raise ValueError(f"{function_name()}: cannot determine site for unit '{unit_name}'")
        if not self._verify_unit_site_membership(site_name, unit_name):
            raise ValueError(f"{function_name()}: cannot set unit config, invalid site/unit membership")

        # Find the 'common' unit config for diffing. `next(..., None)` covers a 'units'
        # collection without a 'common' entry.
        common_conf_dict = next((unit for unit in self._section("units") if unit.get("name") == "common"), None)
        if common_conf_dict is None:
            logger.error(f"{function_name()}: 'common' unit configuration not found")
            raise ValueError(f"{function_name()}: 'common' unit configuration not found")

        # Only store the delta from 'common'
        delta = deep_dict_difference(common_conf_dict, unit_dict) or {}
        if "power_switch" in delta and "network" in delta["power_switch"]:
            saved_power_switch_network = delta["power_switch"]["network"]
            del delta["power_switch"]["network"]
        else:
            saved_power_switch_network = None
        if "name" in delta:
            del delta["name"]

        if not deep_dict_is_empty(delta):
            delta["name"] = unit_name
            if saved_power_switch_network is not None:
                delta.setdefault("power_switch", {})["network"] = saved_power_switch_network

            try:
                self.origin.database()["units"].update_one({"name": unit_name}, {"$set": delta}, upsert=True)
            except PyMongoError:
                # Still only logged, so this call reports success to a caller whose write
                # was lost. Phase 1 raises ConfigError here and reloads 'units' so the
                # writing process sees its own write; both are behaviour changes that
                # belong with the watcher, not with this refactor.
                logger.error(f"{function_name()}: failed to update unit config for {unit_name=} with {delta=}")

    @by_generation("sites")
    def get_sites(self, *, _snapshot: ConfigSnapshot | None = None) -> list[Site]:
        """
        Get all sites from MongoDB configuration
        Returns list of Site objects
        """
        return [Site(**site) for site in self._section("sites", _snapshot)]

    @by_generation("specs")
    def get_thar_filters(self, *, _snapshot: ConfigSnapshot | None = None) -> list[str]:
        doc = self._section("specs", _snapshot)[0]
        return [v for k, v in doc["wheels"]["ThAr"]["filters"].items() if isinstance(v, str) and k != "default"]

    @by_generation("specs")
    def get_specs(self, *, _snapshot: ConfigSnapshot | None = None) -> "SpecsConfig":  # type: ignore # noqa: F821
        from .specs import SpecsConfig

        doc = self._section("specs", _snapshot)[0]

        #
        # For the individual deepspec cameras we merge the camera-specific configuration
        #  with the 'common' configuration
        #
        # Built into a new dict rather than assigned back into `doc["deepspec"][band]`.
        # `doc` is this call's private copy now, so writing into it would no longer reach
        # the store -- but the old shape read as though the merge were meant to persist,
        # which is exactly the habit that made the store diverge from the database.
        deepspec_dict = doc["deepspec"]
        common_dict = deepspec_dict["common"]
        merged: dict[str, Any] = {"common": common_dict}
        for band, band_dict in deepspec_dict.items():
            if band == "common":
                continue
            d = deepcopy(common_dict)
            deep_dict_update(d, band_dict)
            merged[band] = d
        doc["deepspec"] = merged

        return SpecsConfig(**doc)

    @by_generation("services")
    def get_services(self, *, _snapshot: ConfigSnapshot | None = None) -> list[ServiceConfig] | None:
        services = self._section("services", _snapshot)
        if not isinstance(services, list):
            logger.error(f"get_service: expected list, got {type(services)}")
            return None
        return [ServiceConfig(**service) for service in services]

    def get_service(self, service_name: str) -> ServiceConfig | None:
        services = self.get_services()

        assert services is not None
        found = [service for service in services if service.name == service_name]
        if not found:
            logger.error(f"no service named '{service_name}'")
            return None

        return found[0]

    @property
    def vault(self) -> "VaultConfig":
        """Credentials from the share, read once and cached.

        Deliberately unlike the rest of this class in three ways, each for a reason
        recorded in `config/vault.py`: it is NOT reachable from any model that gets
        dumped, it loads on first access rather than in `__init__` (so the share is not
        a startup dependency), and it never raises -- a missing vault degrades whatever
        needed the credential instead of stopping a telescope.
        """
        if Config._vault is None:
            Config._vault = load_vault()
        return Config._vault

    @by_generation("users", "groups")
    def get_users(self, *, _snapshot: ConfigSnapshot | None = None) -> list[UserConfig]:
        all_user_dicts = self._section("users", _snapshot)
        user_configs: list[UserConfig] = []

        all_group_configs = [GroupConfig(**group) for group in self._section("groups", _snapshot)]
        group_config_by_name: dict[str, GroupConfig] = {group.name: group for group in all_group_configs}

        for user_dict in all_user_dicts:
            # `user_dict["capabilities"] = []` used to stand here, injecting a key into
            # the shared store on every call because the model required a field no
            # `users` document has ever carried. `UserConfig.capabilities` now defaults.
            user_config = UserConfig(**user_dict)

            if "everybody" not in user_config.groups:
                user_config.groups.append("everybody")

            for group_name in user_config.groups:
                grp = group_config_by_name.get(group_name)
                if grp is None:
                    logger.warning(f"unknown group '{group_name}' for user '{user_config.name}', ignored!")
                    continue
                for cap in grp.capabilities or []:
                    user_config.capabilities.append(cap)

            user_config.capabilities = sorted(set(user_config.capabilities))  # set() makes unique
            user_configs.append(user_config)

        return user_configs

    def get_user(self, user_name: str) -> UserConfig | None:
        found = [u for u in self.get_users() if u.name == user_name]
        if not found:
            logger.warning(f"no user configuration for '{user_name=}'")
            return None

        return found[0]

    @property
    def sites(self) -> list[Site]:
        return self.get_sites()

    @property
    def local_site(self) -> Site | None:
        # The local site is whatever the config file declares (source of truth),
        # resolved against the DB 'sites' collection by name.
        return next((s for s in self.sites if s.name == self.local.site), None)


def test_specs_config():
    print(json.dumps(Config().get_specs().model_dump(), indent=2))


def test_sites_config():
    sites: list[Site] = Config().sites
    for site in sites:
        print(json.dumps(site.model_dump(), indent=2))


def test_local_site():
    local_site = Config().local_site
    print(json.dumps(local_site.model_dump() if local_site else None, indent=2))


def test_services_config():
    result = Config().get_services()
    assert result is not None
    [print(json.dumps(service.model_dump(), indent=2)) for service in result]


def test_service_config(service_name: str | None):
    result = Config().get_services()
    assert result is not None
    [print(json.dumps(service.model_dump(), indent=2)) for service in result if service.name == service_name]


def test_user(name: str):
    print(json.dumps(Config().get_user(name), indent=2))


def test_unit_config(site_name: str | None = None, unit_name: str | None = None):
    unit_conf = Config().get_unit(site_name=site_name, unit_name=unit_name)
    assert unit_conf is not None
    print(json.dumps(unit_conf.model_dump(), indent=1))


def main():
    # test_specs_config()
    # test_users()

    # test_service_config("control")
    # test_service_config("spec")
    # test_services_config()

    # test_sites_config()
    # test_local_site()
    # test_unit_config(site_name="wis", unit_name="mastw")
    pass


if __name__ == "__main__":
    main()
