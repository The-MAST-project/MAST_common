import io
import json
import logging
import os
import platform
import re
import socket
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import matplotlib.pyplot as plt
import pymongo
import pymongo.database
from cachetools import TTLCache, cached
from PIL import Image
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# from cameras.andor.newton import CoolerMode
from common.const import Const
from common.deep import deep_dict_difference, deep_dict_is_empty, deep_dict_update
from common.mast_logging import init_log

from .identification import GroupConfig, UserConfig
from .site import Site
from .unit import UnitConfig

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
file_cache = TTLCache(maxsize=32, ttl=60)  # 60s TTL
mongo_cache = TTLCache(maxsize=32, ttl=60)  # 60s TTL
config_db_cache = TTLCache(maxsize=100, ttl=30)
DataSource = Literal["file", "mongodb"] | None

#
# Cache management helpers, should be 'manually' called to clear the TTL caches when configuration is changed
#
def clear_file_ttl_cache() -> None:
    file_cache.clear()


def clear_mongo_ttl_cache() -> None:
    mongo_cache.clear()


def _file_cache_key(resolved_path: str, file_mtime: float) -> tuple[str, float]:
    # include mtime so updates invalidate immediately (even before TTL expiry)
    return (resolved_path, file_mtime)


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
        local_config_file: str | None = None,
        mongo_uri: str | None = None,
        database_name: str | None = None,
        collections: tuple[str, ...] | None = None,
    ):
        if self._initialized:
            return

        self.local_config_file: str | None = local_config_file

        self.mongo_uri = mongo_uri
        self.database_name = database_name
        self.collections = collections
        self.query_filter: dict[str, Any] | None = None
        self.client: MongoClient | None = None
        self.db: pymongo.database.Database | None = None

        self.loaded_from: DataSource = None
        self._initialized = True


class Config:
    _instance = None
    _initialized: bool = False

    NUMBER_OF_UNITS = 20

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, site: str | None = None, load_from: DataSource = None):
        """
        - Loads the MAST configuration database.  By default:
          - from local file, if it exists (linux: `~mast/mast-config-db.json`, windows: `C:/MAST/mast-config-db.json`)
          - from MongoDB server
        - If `load_from` is provided, enforces loading ONLY from the specified data source
        """
        if self._initialized:
            return

        if not site:
            """
            This is a bootstrap issue: We need to determine the site based on the hostname before we can send
             database queries to a MAST-{site}-control machine.
            """
            hostname = socket.gethostname()
            site = None
            if hostname.startswith("mast"):
                if hostname[4:] == "w":
                    site = "wis"
                elif hostname[4:] == "00" or (
                    hostname[4:].isdigit()
                    and 1 <= int(hostname[4:]) <= Config.NUMBER_OF_UNITS
                ):
                    # site = "ns"
                    site = "wis"  # until we have a mast-ns-control machine
                else:
                    pat = re.compile(r"^mast-([^-]+)-(?:control|spec)$")
                    m = pat.match(hostname)
                    if m:
                        site = m.group(1)

            if site is None:
                raise ValueError(
                    "Config: cannot deduce site from {hostname=}, please provide site explicitly"
                )

        system = platform.system()
        assert system == "Windows" or system == "Linux"
        file = "mast-config-db.json"
        local_config_file = (
            f"C:/MAST/{file}"
            if system == "Windows"
            else os.path.expanduser("~mast") + f"/{file}"
            if system == "Linux"
            else None
        )
        assert local_config_file is not None

        self.origin = ConfigOrigin(
            local_config_file,
            mongo_uri=f"mongodb://mast-{site}-control:27017",
            database_name="mast",
            collections=("groups", "services", "sites", "specs", "units", "users"),
        )
        self.db: dict = self.get_config()

        self._initialized = True

    # ------------ File backend ------------
    @cached(
        cache=file_cache,
        key=lambda resolved_path, file_mtime: _file_cache_key(
            resolved_path, file_mtime
        ),
    )
    def _load_config_from_file_cached(
        self, resolved_path: str, file_mtime: float
    ) -> dict[str, list[dict[str, Any]]]:
        with open(resolved_path, encoding="utf-8") as fp:
            raw = json.load(fp)

        if not isinstance(raw, dict):
            raise ValueError(
                "Top-level JSON must be an object mapping {collection_name: [documents...]}"
            )

        self.origin.loaded_from = "file"
        self.origin.local_config_file = resolved_path
        normalized: dict[str, list[dict[str, Any]]] = {}
        for collection_name, documents in raw.items():
            if not isinstance(documents, list):
                raise ValueError(
                    f"Collection '{collection_name}' must be a list of documents."
                )
            normalized[collection_name] = documents
        return normalized

    def load_config_from_file(
        self, json_file_path: str
    ) -> dict[str, list[dict[str, Any]]]:
        file_path = Path(json_file_path).resolve()
        stat = file_path.stat()  # raises if missing
        return self._load_config_from_file_cached(str(file_path), stat.st_mtime)

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
        self.origin.loaded_from = "mongodb"
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

    def get_config(self, load_from: DataSource = None) -> dict[str, list[dict[str, Any]]]:

        if self.origin.local_config_file is not None:
            file_path = Path(self.origin.local_config_file)
            if file_path.exists():
                return self.load_config_from_file(str(file_path))

        if load_from == "file": # we were asked to load from local file, but failed
            return {}

        # fallback to Mongo
        if not (
            self.origin.mongo_uri
            and self.origin.database_name
            and self.origin.collections
        ):
            raise ValueError(
                "JSON file not found; provide mongo_uri, database_name, and collections for Mongo fallback."
            )

        return self.load_config_from_mongodb(
            mongo_uri=self.origin.mongo_uri,
            database_name=self.origin.database_name,
            collections=list(self.origin.collections),
            query_filter=self.origin.query_filter,
            drop_object_id=True,
        )

    def get_unit(self, unit_name: str | None = None) -> UnitConfig:
        """
        Gets a unit's configuration.  By default, this is the ['config']['units']['common']
         entry. If a unit-specific entry exists it overrides the 'common' entry.
        """
        units = self.fetch_config_section("units")

        if not unit_name:
            unit_name = socket.gethostname()

        common_config = unit_config = None
        try:
            common_config = [unit for unit in units if unit["name"] == "common"][0]
        except Exception:
            raise ValueError("get_unit: 'common' unit configuration not found")

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
                unit_name.replace("mast", "mastps") + "." + Const.WEIZMANN_DOMAIN
            )
            combined_dict["power_switch"]["network"]["host"] = switch_host_name
            if "ipaddr" not in combined_dict["power_switch"]["network"]:
                try:
                    ipaddr = socket.gethostbyname(switch_host_name)
                    combined_dict["power_switch"]["network"]["ipaddr"] = ipaddr
                except socket.gaierror:
                    logger.warning(f"could not resolve {switch_host_name=}")

        return UnitConfig(**combined_dict)

    def set_unit(self, unit_name: str, unit_conf: UnitConfig):
        unit_dict = unit_conf.model_dump()

        # Find the 'common' unit config for diffing
        try:
            common_conf_dict = [
                unit for unit in self.db["units"] if unit["name"] == "common"
            ][0]
        except Exception:
            logger.error("save_unit_config: 'common' unit configuration not found")
            raise ValueError("save_unit_config: 'common' unit configuration not found")

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

            if self.origin.loaded_from == "mongodb":
                try:
                    assert self.origin.client and self.origin.database_name
                    self.origin.client[self.origin.database_name]["units"].update_one(
                        {"name": unit_name}, {"$set": delta}, upsert=True
                    )
                except PyMongoError:
                    logger.error(
                        f"save_unit_config: failed to update unit config for {unit_name=} with {delta=}"
                    )
                clear_mongo_ttl_cache()

            elif self.origin.loaded_from == "file":
                assert self.origin.local_config_file
                config_path = Path(self.origin.local_config_file)
                if not config_path.exists():
                    raise FileNotFoundError(f"Config file '{config_path}' not found.")

                with open(config_path, encoding="utf-8") as f:
                    config_data = json.load(f)

                units = config_data.get("units", [])
                found = False
                for idx, unit in enumerate(units):
                    if unit.get("name") == unit_name:
                        # Only update the delta fields
                        deep_dict_update(units[idx], delta)
                        found = True
                        break
                if not found:
                    units.append(delta)
                config_data["units"] = units

                # Atomic write: write to temp file, then replace
                dir_name = config_path.parent
                with tempfile.NamedTemporaryFile(
                    "w", dir=dir_name, delete=False, encoding="utf-8"
                ) as tf:
                    json.dump(config_data, tf, indent=2)
                    tempname = tf.name
                os.replace(tempname, config_path)

                clear_file_ttl_cache()

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
        for site in self.db['sites']:
            sites.append(Site(**site))
        
        return sites

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
        found = [s for s in self.sites if s.local]
        if len(found) != 0:
            return found[0]

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

def test_unit_config(name: str | None = None):
    print(json.dumps(Config().get_unit(name).model_dump(), indent=1))


def main():

    # test_specs_config()
    # test_users()

    # test_service_config("control")
    # test_service_config("spec")
    # test_services_config()

    # test_sites_config()
    # test_local_site()
    # test_unit_config(name="mastw")
    pass

if __name__ == "__main__":
    main()
