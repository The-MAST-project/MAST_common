import logging
import socket
from copy import deepcopy

import pymongo
from cachetools import TTLCache, cached
from pydantic import BaseModel, ConfigDict, model_validator
from pymongo.errors import ConnectionFailure, PyMongoError

from common.const import Const
from common.deep import deep_dict_difference, deep_dict_is_empty, deep_dict_update
from common.mast_logging import init_log

logger = logging.getLogger("mast.unit." + __name__)
init_log(logger)

unit_cache = TTLCache(maxsize=100, ttl=30)
sites_cache = TTLCache(maxsize=100, ttl=30)
user_cache = TTLCache(maxsize=100, ttl=30)
users_cache = TTLCache(maxsize=100, ttl=30)
specs_cache = TTLCache(maxsize=100, ttl=30)
service_cache = TTLCache(maxsize=100, ttl=30)


# Enable warning logging for PyMongo
logging.getLogger("pymongo").setLevel(logging.WARNING)


class StagePresets(BaseModel):
    sky: int
    spec: int


class StageConfig(BaseModel):
    presets: StagePresets


class FocuserConfig(BaseModel):
    ascom_driver: str
    known_as_good_position: int


class ClientNetworkConfig(BaseModel):
    host: str
    port: int


class PowerSwitchConfig(BaseModel):
    network: ClientNetworkConfig
    userid: str
    password: str
    timeout: int
    cycle_time: int
    delay_after_on: int
    outlets: dict[int, str]  # {outlet_number: outlet_name}


class CoversConfig(BaseModel):
    ascom_driver: str


class UnitConfig(BaseModel):
    name: str
    power_switch: PowerSwitchConfig
    camera: dict
    stage: StageConfig
    focuser: FocuserConfig
    covers: CoversConfig

    def __init__(self, **data):
        super().__init__(**data)
        self.stage = StageConfig(**data["stage"])
        self.focuser = FocuserConfig(**data["focuser"])
        self.power_switch = PowerSwitchConfig(**data["power_switch"])
        self.camera = data["camera"]
        self.covers = CoversConfig(**data["covers"])
        self.name = data["name"]


class Building(BaseModel):
    names: list[str]
    unit_ids: str | list[str]
    units: list[str] | None = None
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_building(self):
        # self.unit_ids = normalize_unit_specifier(self.unit_ids)
        return self


class Site(BaseModel):
    name: str
    project: str
    deployed_units: list[str] = []
    planned_units: list[str] = []
    units_in_maintenance: list[str] = []
    controller_host: str
    spec_host: str
    domain: str
    local: bool = False
    location: str = "Unknown"
    buildings: list[Building] = []
    unit_ids: str | list[str]

    def normalize_unit_specifier(self, spec) -> list[str]:
        """"""
        ret = []
        specs = []
        if isinstance(spec, list):
            specs = spec
        elif isinstance(spec, str) and "," in spec:
            specs = spec.split(",")
        elif isinstance(spec, str) and "-" in spec:
            low, high = spec.split("-")
            if low.isdigit() and high.isdigit():
                for i in range(int(low), int(high) + 1):
                    specs.append(str(i))
        else:
            specs = [spec]

        for specifier in specs:
            if isinstance(specifier, int):
                ret.append(f"{self.project}{specifier:02}")
            else:
                if not specifier.startswith(self.project):
                    ret.append(
                        f"{self.project}{int(specifier):02}"
                        if specifier.isdigit()
                        else f"{self.project}{specifier}"
                    )
                else:
                    ret.append(specifier)
        return ret

    @model_validator(mode="after")
    def validate_site(self):
        self.deployed_units = self.normalize_unit_specifier(self.deployed_units)
        self.planned_units = self.normalize_unit_specifier(self.planned_units)
        self.units_in_maintenance = self.normalize_unit_specifier(
            self.units_in_maintenance
        )
        self.unit_ids = self.normalize_unit_specifier(self.unit_ids)
        for building in self.buildings:
            building.units = self.normalize_unit_specifier(building.unit_ids)
        return self


class Config:
    _instance = None
    _initialized: bool = False

    NUMBER_OF_UNITS = 20

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, site: str = "wis"):
        if self._initialized:
            return

        try:
            client = pymongo.MongoClient(
                f"mongodb://mast-{site}-control.weizmann.ac.il:27017/"
            )
            self.db = client["mast"]
        except ConnectionFailure as e:
            logger.error(f"{e}")

        self._initialized = True

    @cached(unit_cache)
    def get_unit(self, unit_name: str = None) -> dict:
        """
        Gets a unit's configuration.  By default, this is the ['config']['units']['common']
         entry. If a unit-specific entry exists it overrides the 'common' entry.
        """
        coll = self.db["units"]
        common_conf = coll.find_one({"name": "common"})
        del common_conf["_id"]
        ret: dict = deepcopy(common_conf)

        if not unit_name:
            unit_name = socket.gethostname()

        # override with unit-specific config
        unit_conf: dict = coll.find_one({"name": unit_name})
        del unit_conf["_id"]
        if unit_conf:
            deep_dict_update(ret, unit_conf)

        # resolve power-switch name and ipaddr
        if unit_name:
            ret["name"] = unit_name
            if ret["power_switch"]["network"]["host"] == "auto":
                switch_host_name = (
                    unit_name.replace("mast", "mastps") + "." + Const.WEIZMANN_DOMAIN
                )
                ret["power_switch"]["network"]["host"] = switch_host_name
                if "ipaddr" not in ret["power_switch"]["network"]:
                    try:
                        ipaddr = socket.gethostbyname(switch_host_name)
                        ret["power_switch"]["network"]["ipaddr"] = ipaddr
                    except socket.gaierror:
                        logger.warning(f"could not resolve {switch_host_name=}")

        return ret

    def set_unit(self, unit_name: str = None, unit_conf: dict = None):
        if not unit_name:
            raise Exception("save_unit_config: 'unit_name' cannot be None")
        if not unit_conf:
            raise Exception("save_unit_config: 'unit_conf' cannot be None")

        common_conf = self.db["units"].find_one({"name": "common"})
        del common_conf["_id"]
        difference = deep_dict_difference(common_conf, unit_conf)
        saved_power_switch_network = difference["power_switch"]["network"]
        del difference["power_switch"]["network"]
        del difference["name"]

        if not deep_dict_is_empty(difference):
            difference["name"] = unit_name
            difference["power_switch"]["network"] = saved_power_switch_network
            try:
                self.db["units"].update_one(
                    {"name": unit_name}, {"$set": difference}, upsert=True
                )
            except PyMongoError:
                logger.error(
                    f"save_unit_config: failed to update unit config for {unit_name=} with {difference=}"
                )

    @cached(sites_cache)
    def get_sites(self) -> list[Site]:
        ret = []
        for d in self.db["sites"].find():
            del d["_id"]
            ret.append(Site(**d))
        return ret

    @cached(specs_cache)
    def get_specs(self) -> dict:
        doc = self.db["specs"].find()[0]

        #
        # For the individual deepspec cameras we merge the camera-specific configuration
        #  with the 'common' configuration
        #
        deepspec_conf = doc["deepspec"]
        common = deepspec_conf["common"]
        bands = [k for k in deepspec_conf if k != "common"]
        for band in bands:
            d = deepcopy(common)
            deep_dict_update(d, deepspec_conf[band])
            doc["deepspec"][band] = d

        return {
            "wheels": doc["wheels"],
            "gratings": doc["gratings"],
            "power_switch": doc["power_switch"],
            "stage": doc["stage"],
            "chiller": doc["chiller"],
            "deepspec": doc["deepspec"],
            "highspec": doc["highspec"],
            "lamps": doc["lamps"],
        }

    @cached(service_cache)
    def get_service(self, service_name: str) -> dict:
        try:
            doc = self.db["services"].find_one({"name": service_name})
        except PyMongoError as e:
            logger.error(f"could not get 'services' (error={e})")
            raise
        return doc

    @cached(user_cache)
    def get_user(self, name: str = None) -> dict:
        try:
            user = self.db["users"].find_one({"name": name})
            groups: list = user["groups"]
        except PyMongoError:
            logger.error(f"failed to get user {name=}")
            raise
        groups.append("everybody")

        collection = self.db["groups"]
        # Define the aggregation pipeline
        pipeline = [
            {"$match": {"name": {"$in": groups}}},
            {"$unwind": "$capabilities"},
            {
                "$group": {
                    "_id": None,
                    "allCapabilities": {"$addToSet": "$capabilities"},
                }
            },
            {"$project": {"_id": 0, "allCapabilities": 1}},
            {"$unwind": "$allCapabilities"},
            {"$sort": {"allCapabilities": 1}},
            {
                "$group": {
                    "_id": None,
                    "sortedCapabilities": {"$push": "$allCapabilities"},
                }
            },
            {"$project": {"_id": 0, "sortedCapabilities": 1}},
        ]

        # Perform the aggregation
        result = list(collection.aggregate(pipeline))

        # Extract the list of all capabilities
        capabilities = []
        if result:
            capabilities = result[0]["sortedCapabilities"]

        return {"name": name, "groups": groups, "capabilities": capabilities}

    @cached(users_cache)
    def get_users(self) -> list[str]:
        users = []
        for user in self.db["users"].find():
            users.append(user["name"])
        return users

    @property
    def sites(self) -> list[Site]:
        return self.get_sites()

    @property
    def local_site(self) -> Site:
        return [s for s in self.sites if s.local][0]


if __name__ == "__main__":
    import json

    # print(json.dumps(Config().get_specs(), indent=2))
    # print(json.dumps(Config().get_sites(), indent=2))
    # print(json.dumps(Config().get_users(), indent=2))

    print(json.dumps([s.to_dict() for s in Config().sites]))
