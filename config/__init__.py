import io
import json
import logging
import socket
from copy import deepcopy
from typing import Any

import matplotlib.pyplot as plt
import pymongo
import pymongo.database
from cachetools import TTLCache, cached
from PIL import Image
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from common.deep import deep_dict_difference, deep_dict_is_empty, deep_dict_update
from common.mast_logging import init_log
from common.utils import function_name

from .identification import GroupConfig, UserConfig
from .local import ConfigError, LocalConfig, load_local_config
from .site import Site
from .unit import UnitConfig

# The collections that make up the MAST configuration database. This is the DB
# schema/layout (not a per-deployment setting), so it stays a module constant.
DEFAULT_COLLECTIONS = ("groups", "services", "sites", "specs", "units", "users")

logger = logging.getLogger("mast.unit." + __name__)
init_log(logger)


# Enable warning logging for PyMongo
logging.getLogger("pymongo").setLevel(logging.WARNING)


class ServiceConfig(BaseModel):
    name: str
    listen_on: str = "0.0.0.0"
    port: int = 8000


#
# configuration caching
#
mongo_cache = TTLCache(maxsize=32, ttl=60)  # 60s TTL
config_db_cache = TTLCache(maxsize=100, ttl=30)


#
# Cache management helpers, should be 'manually' called to clear the TTL caches when configuration is changed
#
def clear_mongo_ttl_cache() -> None:
    mongo_cache.clear()


def _mongo_cache_key(
    mongo_uri: str,
    database_name: str,
    collections_tuple: tuple[str, ...],
    query_filter_json: str | None,
    drop_object_id: bool,
) -> tuple[Any, ...]:
    return (
        mongo_uri,
        database_name,
        collections_tuple,
        query_filter_json,
        drop_object_id,
    )


class ConfigOrigin:
    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        mongo_uri: str | None = None,
        database_name: str | None = None,
        collections: tuple[str, ...] | None = None,
    ):
        if self._initialized:
            return

        self.mongo_uri = mongo_uri
        self.database_name = database_name
        self.collections = collections
        self.query_filter: dict[str, Any] | None = None
        self.client: MongoClient | None = None
        self.db: pymongo.database.Database | None = None

        self._initialized = True


class Config:
    _instance = None
    _initialized: bool = False

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
        )
        self.db: dict = self.get_config()
        self._validate_local_identity()

        self._initialized = True

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
                mismatches.append(
                    f"  - {field}: config file = {local_value!r}, DB site = {db_value!r}"
                )
        for attr in ("latitude", "longitude", "elevation"):
            local_value = getattr(self.local.location, attr)
            db_value = getattr(db_site.location, attr)
            if local_value != db_value:
                mismatches.append(
                    f"  - location.{attr}: config file = {local_value!r}, "
                    f"DB site = {db_value!r}"
                )
        if mismatches:
            raise ConfigError(
                f"local configuration for site '{self.local.site}' disagrees with the "
                "DB 'sites' document (these must match):\n" + "\n".join(mismatches)
            )

    # ------------ MongoDB backend ------------
    @cached(
        cache=mongo_cache,
        key=lambda *args, **kwargs: _mongo_cache_key(*args[1:], **kwargs),  # skip self
    )
    def _load_config_from_mongodb_cached(
        self,
        mongo_uri: str,
        database_name: str,
        collections_tuple: tuple[str, ...],
        query_filter_json: str | None,
        drop_object_id: bool,
    ) -> dict[str, list[dict[str, Any]]]:
        self.origin.collections = collections_tuple
        self.origin.database_name = database_name
        self.origin.mongo_uri = mongo_uri

        if MongoClient is None:
            raise RuntimeError(
                "pymongo is not installed but MongoDB source was requested."
            )
        client = MongoClient(mongo_uri)
        self.origin.client = client
        self.origin.db = self.origin.client[database_name]
        self.origin.query_filter = (
            json.loads(query_filter_json) if query_filter_json else {}
        )
        result: dict[str, list[dict[str, Any]]] = {}
        for collection_name in collections_tuple:
            cursor = self.origin.db[collection_name].find(
                self.origin.query_filter,
                projection=None if not drop_object_id else {"_id": False},
            )
            result[collection_name] = list(cursor)
        return result

    def load_config_from_mongodb(
        self,
        mongo_uri: str,
        database_name: str,
        collections: list[str],
        query_filter: dict[str, Any] | None = None,
        drop_object_id: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        collections_tuple = tuple(collections)
        query_filter_json = (
            json.dumps(query_filter, sort_keys=True) if query_filter else None
        )
        return self._load_config_from_mongodb_cached(
            mongo_uri,
            database_name,
            collections_tuple,
            query_filter_json,
            drop_object_id,
        )

    def get_config(self) -> dict[str, list[dict[str, Any]]]:
        if not (
            self.origin.mongo_uri
            and self.origin.database_name
            and self.origin.collections
        ):
            raise ConfigError(
                "missing mongo_uri, database, or collections; cannot load configuration."
            )

        return self.load_config_from_mongodb(
            mongo_uri=self.origin.mongo_uri,
            database_name=self.origin.database_name,
            collections=list(self.origin.collections),
            query_filter=self.origin.query_filter,
            drop_object_id=True,
        )

    def _verify_unit_site_membership(self, site_name: str, unit_name: str) -> bool:
        unit_name = unit_name.lower()
        sites = self.get_sites()
        site = [s for s in sites if s.name == site_name]
        if not site:
            logger.error(f"{function_name()}: no site named '{site_name}'")
            return False
        if unit_name not in site[0].unit_ids:
            logger.error(
                f"{function_name()}: site '{site_name}' has no unit named '{unit_name}'"
            )
            return False
        return True

    def site_name_from_unit_name(self, unit_name: str) -> str | None:
        unit_name = unit_name.lower()
        sites = self.get_sites()
        for site in sites:
            if unit_name in site.unit_ids:
                return site.name
        return None

    def get_unit(
        self, site_name: str | None = None, unit_name: str | None = None
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
            site_name = (
                self.local.site
                if local_unit
                else self.site_name_from_unit_name(unit_name)
            )
            if site_name is None:
                logger.error(
                    f"{function_name()}: cannot determine site for unit '{unit_name}'"
                )
                return None

        if not self._verify_unit_site_membership(site_name, unit_name or ""):
            return None

        units = self.fetch_config_section("units")
        if unit_name not in [unit["name"] for unit in units]:
            return None

        common_config = unit_config = None
        try:
            common_config = [unit for unit in units if unit["name"] == "common"][0]
        except Exception as ex:
            raise ValueError("get_unit: 'common' unit configuration not found") from ex

        try:
            found = [unit for unit in units if unit["name"] == unit_name]
            unit_config = found[0]
        except Exception:
            unit_config = None  # we may not hve a unit-specific entry in the DB

        combined_dict: dict = deepcopy(common_config)
        if unit_config:
            deep_dict_update(combined_dict, unit_config)

        # resolve power-switch name and ipaddr
        combined_dict["name"] = unit_name
        if combined_dict["power_switch"]["network"]["host"] == "auto":
            switch_host_name = (
                unit_name.replace("mast", "mastps") + "." + self.local.domain
            )
            combined_dict["power_switch"]["network"]["host"] = switch_host_name
            if "ipaddr" not in combined_dict["power_switch"]["network"]:
                try:
                    ipaddr = socket.gethostbyname(switch_host_name)
                    combined_dict["power_switch"]["network"]["ipaddr"] = ipaddr
                except socket.gaierror:
                    logger.warning(f"could not resolve {switch_host_name=}")

        try:
            ret = UnitConfig(**combined_dict)
        except Exception as ex:
            logger.error(
                f"get_unit: failed to parse unit configuration for {unit_name=}: {ex}"
            )
            raise ex
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
            site_name = (
                self.local.site
                if local_unit
                else self.site_name_from_unit_name(unit_name)
            )
            if site_name is None:
                raise ValueError(
                    f"{function_name()}: cannot determine site for unit '{unit_name}'"
                )
        if not self._verify_unit_site_membership(site_name, unit_name):
            raise ValueError(
                f"{function_name()}: cannot set unit config, invalid site/unit membership"
            )

        # Find the 'common' unit config for diffing
        try:
            common_conf_dict = [
                unit for unit in self.db["units"] if unit["name"] == "common"
            ][0]
        except Exception:
            logger.error(f"{function_name()}: 'common' unit configuration not found")
            raise ValueError(
                f"{function_name()}: 'common' unit configuration not found"
            )

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
                delta.setdefault("power_switch", {})["network"] = (
                    saved_power_switch_network
                )

            try:
                assert self.origin.client and self.origin.database_name
                self.origin.client[self.origin.database_name]["units"].update_one(
                    {"name": unit_name}, {"$set": delta}, upsert=True
                )
            except PyMongoError:
                logger.error(
                    f"{function_name()}: failed to update unit config for {unit_name=} with {delta=}"
                )
            clear_mongo_ttl_cache()

    @cached(config_db_cache)
    def config_db(self):
        return self.db  # cache it

    def fetch_config_section(self, section: str):
        db = self.config_db()

        assert db is not None and section in db
        return db[section]

    def get_sites(self) -> list[Site]:
        """
        Get all sites from MongoDB configuration
        Returns list of Site objects
        """

        sites = []
        for site in self.db["sites"]:
            sites.append(Site(**site))

        return sites

    def get_thar_filters(self) -> list[str]:
        doc = self.fetch_config_section("specs")[0]
        return [
            v
            for k, v in doc["wheels"]["ThAr"]["filters"].items()
            if isinstance(v, str) and k != "default"
        ]

    def get_specs(self) -> "SpecsConfig":  # type: ignore # noqa: F821
        from .specs import SpecsConfig

        doc = self.fetch_config_section("specs")[0]

        #
        # For the individual deepspec cameras we merge the camera-specific configuration
        #  with the 'common' configuration
        #
        deepspec_dict = doc["deepspec"]
        common_dict = deepspec_dict["common"]
        bands = [k for k in deepspec_dict if k != "common"]
        for band in bands:
            d = deepcopy(common_dict)
            deep_dict_update(d, deepspec_dict[band])
            doc["deepspec"][band] = d

        return SpecsConfig(**doc)

    def get_services(self) -> list[ServiceConfig] | None:
        services = self.fetch_config_section("services")
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

    def get_users(self) -> list[UserConfig]:
        all_user_dicts = self.fetch_config_section("users")
        user_configs: list[UserConfig] = []

        all_group_configs: list[GroupConfig] = [
            GroupConfig(**group) for group in self.fetch_config_section("groups")
        ]
        all_group_names = [group.name for group in all_group_configs]

        for user_dict in all_user_dicts:
            user_dict["capabilities"] = []
            user_config = UserConfig(**user_dict)

            if "everybody" not in user_config.groups:
                user_config.groups.append("everybody")

            for group_name in user_config.groups:
                if group_name not in all_group_names:
                    logger.warning(
                        f"unknown group '{group_name}' for user '{user_config.name}', ignored!"
                    )
                    continue
                grp = [
                    group_config
                    for group_config in all_group_configs
                    if group_config.name == group_name
                ][0]
                for cap in grp.capabilities or []:
                    user_config.capabilities.append(cap)

            user_config.capabilities = sorted(
                set(user_config.capabilities)
            )  # set() makes unique
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
    [
        print(json.dumps(service.model_dump(), indent=2))
        for service in result
        if service.name == service_name
    ]


def test_users():
    for conf in Config().get_users():
        if conf.picture:
            img = Image.open(io.BytesIO(conf.picture))
            plt.imshow(img)
            plt.axis("off")  # Hide axes
            plt.show()
        else:
            print(f"no picture for user '{conf.name}'")
        print(json.dumps(conf.model_dump(), indent=2))


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
