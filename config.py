import io
import logging
import socket
from copy import deepcopy
from typing import Literal

import ASI
import matplotlib.pyplot as plt
import pymongo
from cachetools import TTLCache, cached
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pymongo.errors import ConnectionFailure, PyMongoError

from cameras.andor.newton import common.ASI as CoolerMode
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


class UserConfig(BaseModel):
    name: str
    full_name: str | None = None
    groups: list[str]
    capabilities: list[str]
    picture: bytes | None = Field(default=None, exclude=True)
    email: str | None = None
    password: str | None = None
    model_config = {"arbitrary_types_allowed": True}


class ServiceConfig(BaseModel):
    name: str
    listen_on: str = "0.0.0.0"
    port: int = 8000


class ImagerBinningConfig(BaseModel):
    """Configuration for the imager binning."""

    x: int
    y: int

    @model_validator(mode="after")
    def validate_binning(self):
        if self.x <= 0 or self.y <= 0:
            raise ValueError("Binning values must be positive integers.")
        return self


class SkyRoiConfig(BaseModel):
    """Configuration for the region of interest (ROI) in the sky image."""

    sky_x: int
    sky_y: int
    width: int
    height: int


class SpecRoiConfig(BaseModel):
    """Configuration for the region of interest (ROI) in the spectrograph."""

    width: int
    height: int
    fiber_x: int
    fiber_y: int


class NetworkConfig(BaseModel):
    """Network configuration for components that need network connectivity."""

    host: str | None = None
    port: int = 80
    ipaddr: str | None = None

    @model_validator(mode="after")
    def validate_network(self):
        if self.host is None and self.ipaddr is None:
            raise ValueError("Either 'host' or 'ipaddr' must be provided.")
        if self.port <= 0 or self.port > 65535:
            raise ValueError("Port must be a valid TCP port number (1-65535).")

        if self.host is None and self.ipaddr is not None:  # if only ipaddr is provided
            try:
                self.host, _, _ = socket.gethostbyaddr(self.ipaddr)
            except socket.herror:
                logger.warning(
                    f"Could not resolve IP address {self.ipaddr}, host will be None"
                )
                self.host = None
        elif self.ipaddr is None and self.host is not None:  # if only host is provided
            try:
                self.ipaddr = socket.gethostbyname(self.host)
            except socket.gaierror:
                logger.warning(
                    f"Could not resolve host {self.host}, ipaddr will be None"
                )
                self.ipaddr = None
        return self


class StagePresets(BaseModel):
    """Configuration for stage preset positions."""

    sky: int
    spec: int


class StageConfig(BaseModel):
    """Configuration for the telescope stage."""

    presets: StagePresets


class FocuserConfig(BaseModel):
    """Configuration for the telescope focuser."""

    ascom_driver: str
    known_as_good_position: int


class PowerSwitchOutlet(BaseModel):
    """Configuration for a single power switch outlet."""

    name: str
    number: int


class OutletConfig(BaseModel):
    outlet: int  # the outlet number
    switch: str  # name of the power-switch
    delay_after_on: int = 0  # delay in seconds after switching on the outlet


class PowerConfig(BaseModel):
    power: OutletConfig


class PowerSwitchConfig(BaseModel):
    """Configuration for the power switch that controls unit components."""

    network: NetworkConfig
    userid: str
    password: str
    timeout: int = 0
    cycle_time: int = 0
    delay_after_on: int = 0
    outlets: dict[Literal["1", "2", "3", "4", "5", "6", "7", "8"], str]


class OffsetConfig(BaseModel):
    x: int
    y: int


class RoiConfig(BaseModel):
    """Configuration for the region of interest (ROI) in the camera."""

    x: int
    y: int
    width: int
    height: int


class ImagerConfig(BaseModel):
    """Configuration for the imager."""

    imager_type: str
    valid_imager_types: list[str]
    # power: PowerSwitchConfig | None = None
    offset: OffsetConfig | None = None
    roi: RoiConfig | None = None
    temp_check_interval: int = 60
    pixel_scale_at_bin1: float
    format: ASI.ValidOutputFormats
    gain: int


class CoversConfig(BaseModel):
    """Configuration for the telescope covers."""

    ascom_driver: str


class MountConfig(BaseModel):
    """Configuration for the telescope mount."""

    ascom_driver: str


class PHD2SettleConfig(BaseModel):
    """Configuration for PHD2 settle settings."""

    pixels: int
    time: int
    timeout: int


class PHD2Config(BaseModel):
    profile: str
    settle: PHD2SettleConfig
    validation_interval: float


class ToleranceConfig(BaseModel):
    """Configuration for the acquisition tolerances."""

    ra_arcsec: float
    dec_arcsec: float


class AcquisitionConfig(BaseModel):
    """Configuration for the acquisition settings."""

    exposure: float
    binning: ImagerBinningConfig
    tolerance: ToleranceConfig
    tries: int
    gain: int
    roi: SkyRoiConfig


class GuidingConfig(BaseModel):
    """Configuration for guiding settings."""

    exposure: float
    binning: ImagerBinningConfig
    tolerance: ToleranceConfig
    gain: int
    min_ra_correction_arcsec: float
    min_dec_correction_arcsec: float
    cadence_seconds: int
    roi: SpecRoiConfig


class GuiderConfig(BaseModel):
    method: str
    valid_methods: list[str]


class SolvingConfig(BaseModel):
    method: str
    valid_methods: list[str]


class AutofocusConfig(BaseModel):
    """Configuration for autofocus settings."""

    exposure: float
    binning: ImagerBinningConfig
    roi: RoiConfig | None = None
    images: int
    spacing: int
    max_tolerance: int
    max_tries: int


class UnitConfig(BaseModel):
    """Complete configuration for a MAST unit.

    This is the top-level configuration class that contains all settings
    needed to configure and operate a MAST unit.
    """

    name: str
    power_switch: PowerSwitchConfig
    imager: ImagerConfig
    stage: StageConfig
    focuser: FocuserConfig
    covers: CoversConfig
    mount: MountConfig
    phd2: PHD2Config
    acquisition: AcquisitionConfig
    solving: SolvingConfig
    guiding: GuidingConfig
    autofocus: AutofocusConfig
    guider: GuiderConfig

    @model_validator(mode="after")
    def validate_unit_config(self):
        # conditions = [self.guider.method == "phd2", self.imager.imager_type == "phd2"]

        # if any(conditions) and not all(conditions):
        #     raise ValidationError(
        #         f"if any of {self.imager.imager_type=} or {self.guider.method=} is 'phd2', then BOTH must be 'phd2'"
        #     )

        return self


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

        # "wheels": doc["wheels"],
        # "gratings": doc["gratings"],
        # "power_switch": doc["power_switch"],
        # "stage": doc["stage"],
        # "chiller": doc["chiller"],
        # "deepspec": doc["deepspec"],
        # "highspec": doc["highspec"],
        # "lamps": doc["lamps"],


# Filter wheels


# Configuration for a single wheel
class WheelConfig(BaseModel):
    serial_number: str
    filters: dict[str, str]
    power: OutletConfig

    @model_validator(mode="after")
    def validate_wheel(self):
        """
        A filter wheel must have an "Empty" filter.

        The "default" filter is used for the default position of the wheel (at startup).
        If supplied, it must be the value one of the filters.
        If not supplied it defaults to the "Empty" filter.
        """
        if not self.serial_number:
            raise ValueError("Wheel serial number must be provided.")
        if not self.filters:
            raise ValueError("Wheel filters must be defined.")

        valid_filter_names = ["1", "2", "3", "4", "5", "6", "default"]
        for key in str(self.filters.keys()):
            if key not in valid_filter_names:
                raise ValidationError(f"filter name {key} not in {valid_filter_names} ")
        if "default" not in self.filters:
            self.filters["default"] = "Empty"
        elif self.filters["default"] not in valid_filter_names:
            raise ValueError(
                f"Default filter '{self.filters['default']}' must be one of the defined filters."
            )
        return self


class GratingConfig(BaseModel):
    position: int  # for focusing the HighSpec camera


HighspecPresets = Literal["Ca", "Halpha", "Mg", "Future"]
DeepspecPresets = Literal["deepspec", "highspec"]
StagePresetNames = [HighspecPresets, DeepspecPresets]


class SpecStageConfig(BaseModel):
    """Configuration for the spectrograph stages"""

    peripheral: str
    presets: dict[str, int]
    startup_preset: str | None
    shutdown_preset: str | None

    @model_validator(mode="after")
    def validate_spec_stage_config(self):
        for name in [self.presets.keys(), self.startup_preset, self.shutdown_preset]:
            if name not in StagePresetNames:
                raise ValidationError(f"{name=} not in {StagePresetNames}")
        return self


class SpecStageControllerConfig(BaseModel):
    """Configuration for the spectrograph stages controller."""

    network: NetworkConfig
    power: OutletConfig


class FiberStageConfig(BaseModel):
    peripheral: str
    presets: dict[str, int]
    startup_preset: str | None
    shutdown_preset: str | None

    @model_validator(mode="after")
    def validate_spec_stage_config(self):
        for name in [self.presets.keys(), self.startup_preset, self.shutdown_preset]:
            if name not in StagePresetNames:
                raise ValidationError(f"{name=} not in {StagePresetNames}")
        return self


class SpecStagesConfig(BaseModel):
    """Configuration for the spectrograph stages controller."""

    controller: SpecStageControllerConfig
    fiber: FiberStageConfig
    disperser: SpecStageConfig
    focusing: SpecStageConfig


class ChillerConfig(BaseModel):
    power: OutletConfig


class GreateyesTemperatureConfig(BaseModel):
    """Configuration for Greateyes temperature settings."""

    target_cool: float = -5.0  # Default target temperature in Celsius
    target_warm: float = 0.0  # Temperature hysteresis in Celsius
    check_interval: int = 30  # Interval to check temperature in seconds


class GreateyesCropConfig(BaseModel):
    col: int = 1056
    line: int = 1027
    enabled: bool = False


class ShutterConfig(BaseModel):
    """Configuration for Greateyes shutter settings."""

    open_time: int  # time it takes to open (ms)
    close_time: int  # time it takes to close (ms)
    automatic: bool = True  # Whether the shutter operates automatically


class GreateyesReadoutConfig(BaseModel):
    speed: int
    mode: int = 2


class GreateyesProbingConfig(BaseModel):
    boot_delay: int = 25  # seconds to wait after booting the camera
    interval: int = 60  # seconds to check the camera status


class GreateyesSettingConfig(BaseModel):
    """Configuration for Greateyes settings."""

    binning: ImagerBinningConfig | None = None  # Binning configuration for the camera
    bytes_per_pixel: int = 4  # Default bytes per pixel for Greateyes camera
    number_of_exposures: int = 1
    exposure_duration: float = 5.0  # Default exposure duration in seconds
    temp: GreateyesTemperatureConfig
    crop: GreateyesCropConfig
    shutter: ShutterConfig
    readout: GreateyesReadoutConfig
    probing: GreateyesProbingConfig

    @model_validator(mode="after")
    def validate_greateyes_setting(self):
        if self.binning is None:
            self.binning = ImagerBinningConfig(x=1, y=1)
        return self


class GreateyesConfig(BaseModel):
    network: NetworkConfig | None = None  # Network configuration for Greateyes device
    power: OutletConfig | None = None  # Power switch configuration
    enabled: bool | None = True
    device: int | None = None  # Device number
    settings: GreateyesSettingConfig | None = None  # Camera settings


class DeepspecConfig(BaseModel):
    dict[Literal["G", "I", "U", "R", "common"], GreateyesConfig]


class ServerConfig(BaseModel):
    """Configuration for the server."""

    host: str | None = None  # IP address on which the server will listen
    port: int = 8002

    @model_validator(mode="after")
    def validate_server_config(self):
        if self.host is None:
            self.host = "0.0.0.0"  # Default to all interfaces
        return self


class NewtonTemperatureConfig(BaseModel):
    """Configuration for the Newton camera temperature settings."""

    set_point: int = -10  # Default target temperature in Celsius
    cooler_mode: CoolerMode = CoolerMode.RETURN_TO_AMBIENT


class NewtonSettingsConfig(BaseModel):
    """Configuration for the Newton camera settings."""

    binning: ImagerBinningConfig | None = None  # Binning configuration for the camera
    shutter: ShutterConfig
    acquisition_mode: int = 1  # Default acquisition mode
    number_of_exposures: int = 1
    exposure_duration: float = 5.0  # Default exposure duration in seconds
    em_gain: int = 254  # Default EM gain value
    pre_amp_gain: int = 0  # Default pre-amplifier gain value
    temperature: NewtonTemperatureConfig
    read_mode: int

    @model_validator(mode="after")
    def validate_newton_settings(self):
        if self.binning is None:
            self.binning = ImagerBinningConfig(x=1, y=1)
        return self


class HighspecConfig(BaseModel):
    """Configuration for the Newton camera."""

    power: OutletConfig
    settings: NewtonSettingsConfig


class SpecsConfig(BaseModel):
    """Configuration for the spectrograph."""

    wheels: dict[str, WheelConfig]
    gratings: dict[str, GratingConfig]
    power_switch: dict[str, PowerSwitchConfig]
    stage: SpecStagesConfig
    chiller: ChillerConfig
    deepspec: dict[str, GreateyesConfig]
    highspec: HighspecConfig
    lamps: dict[str, PowerConfig]
    server: ServerConfig

    @model_validator(mode="after")
    def validate_specs_config(self):
        # filter wheels
        valid_wheel_names = ["ThAr", "qTh"]
        for name in str(self.wheels.keys()):
            if name not in valid_wheel_names:
                raise ValidationError(
                    f"SpecsConfig: invalid wheel name '{name}', '{valid_wheel_names=}'"
                )

        # gratings
        valid_grating_names = ["Halpha", "Mg", "Ca", "Future"]
        for name in str(self.gratings.keys()):
            if name not in valid_grating_names:
                raise ValidationError(
                    f"SpecsConfig: invalid grating name '{name}', {valid_grating_names=}"
                )

        valid_lamp_names = valid_wheel_names
        for name in str(self.lamps.keys()):
            if name not in valid_lamp_names:
                raise ValidationError(
                    f"SpecsConfig: invalid lamp name '{name}', '{valid_lamp_names=}'"
                )

        valid_deepspec_camera_names = ["G", "I", "U", "R", "common"]
        for name in str(self.deepspec.keys()):
            if name not in valid_deepspec_camera_names:
                raise ValidationError(
                    f"SpecsConfig: invalid deepspec camera name '{name}', {valid_deepspec_camera_names=}"
                )

        if "common" not in self.deepspec:
            raise ValidationError(
                "SpecsConfig: 'common' deepspec configuration not found"
            )

        common_cfg = self.deepspec.get("common")
        if not common_cfg:
            raise ValidationError(
                "SpecsConfig: 'common' deepspec configuration is None"
            )

        if any([common_cfg.network, common_cfg.power, common_cfg.device]):
            raise ValidationError(
                "SpecsConfig: 'common' deepspec configuration should not have network, power or device set"
            )
        if not common_cfg.settings:
            raise ValidationError(
                "SpecsConfig: 'common' deepspec configuration must have settings set"
            )

        for band in self.deepspec:
            if band == "common":
                continue

            band_cfg = self.deepspec[band]
            if not band_cfg:
                raise ValidationError(
                    f"SpecsConfig: '{band}' deepspec configuration not found"
                )

            if not band_cfg.network or not band_cfg.power or band_cfg.device is None:
                raise ValidationError(
                    f"SpecsConfig: '{band}' deepspec configuration must have network, power and device set"
                )

            # Copy common settings to each band
            if not band_cfg.settings:
                band_cfg.settings = deepcopy(common_cfg.settings)
            else:
                deep_dict_update(
                    band_cfg.settings.model_dump(), common_cfg.settings.model_dump()
                )
            pass
        return self


class Config:
    _instance = None
    _initialized: bool = False

    NUMBER_OF_UNITS = 20

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, site: str | None = None):
        if self._initialized:
            return

        if not site:
            """
            This is a bootstrap issue: We need to determine the site based on the hostname before we can send
             database queries to a MAST-{site}-control machine.
            """
            hostname = socket.gethostname()
            site = "unknown"
            if hostname.startswith("mast"):
                if hostname[4:] == "w":
                    site = "wis"
                elif hostname[4:] == "00" or (
                    hostname[4:].isdigit()
                    and 1 <= int(hostname[4:]) <= Config.NUMBER_OF_UNITS
                ):
                    # site = "ns"
                    site = "wis"  # until we have a mast-ns-control machine
            if site == "unknown":
                raise ValueError(
                    "Config: cannot deduce site from {hostname=}, please provide site explicitly"
                )
        try:
            client = pymongo.MongoClient(
                f"mongodb://mast-{site}-control.weizmann.ac.il:27017/"
            )
            self.db = client["mast"]
        except ConnectionFailure as e:
            logger.error(f"{e}")

        self._initialized = True

    @cached(unit_cache)
    def get_unit(self, unit_name: str | None = None) -> UnitConfig:
        """
        Gets a unit's configuration.  By default, this is the ['config']['units']['common']
         entry. If a unit-specific entry exists it overrides the 'common' entry.
        """
        collection = self.db["units"]
        common_dict = collection.find_one({"name": "common"})
        if common_dict is None:
            logger.error("get_unit: 'common' unit configuration not found")
            raise ValueError("get_unit: 'common' unit configuration not found")

        del common_dict["_id"]
        combined_dict: dict = deepcopy(common_dict)

        if not unit_name:
            unit_name = socket.gethostname()

        # override with unit-specific config
        unit_dict = collection.find_one({"name": unit_name})
        if unit_dict is None:
            logger.warning(
                f"get_unit: no unit configuration found for {unit_name=}, using 'common' config"
            )
            unit_dict = {}
        del unit_dict["_id"]
        if unit_dict:
            deep_dict_update(combined_dict, unit_dict)

        # resolve power-switch name and ipaddr
        if unit_name:
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

        common_conf = self.db["units"].find_one({"name": "common"})
        if common_conf is None:
            logger.error("save_unit_config: 'common' unit configuration not found")
            raise ValueError("save_unit_config: 'common' unit configuration not found")

        del common_conf["_id"]
        if difference := deep_dict_difference(common_conf, unit_dict):
            saved_power_switch_network = difference["power_switch"]["network"]
            del difference["power_switch"]["network"]
            if "name" in difference:
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
    def get_specs(self) -> SpecsConfig:
        doc = self.db["specs"].find()[0]

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

    @cached(service_cache)
    def get_service(self, service_name: str) -> ServiceConfig | None:
        try:
            doc = self.db["services"].find_one({"name": service_name})
        except PyMongoError as e:
            logger.error(f"could not get 'services' (error={e})")
            raise

        return ServiceConfig(**doc) if doc else None

    @cached(user_cache)
    def get_user(self, name: str) -> UserConfig:
        user = {}
        try:
            user = self.db["users"].find_one({"name": name})
        except PyMongoError as ex:
            logger.error(f"failed to get user {name=}, {ex=}")
            raise

        groups: list = user["groups"] if user else []
        if "everybody" not in groups:
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

        return UserConfig(
            name=name,
            full_name=user["full_name"] if user and "full_name" in user else None,
            password=user["password"] if user and "password" in user else None,
            email=user["email"] if user and "email" in user else None,
            picture=user["picture"] if user and "picture" in user else None,
            groups=groups,
            capabilities=capabilities,
        )

    @cached(users_cache)
    def get_users(self) -> list[UserConfig]:
        users: list[UserConfig] = []
        for user in self.db["users"].find():
            users.append(self.get_user(user["name"]))
        return users

    @property
    def sites(self) -> list[Site]:
        return self.get_sites()

    @property
    def local_site(self) -> Site:
        return [s for s in self.sites if s.local][0]


if __name__ == "__main__":
    import json

    # print(json.dumps(Config().get_specs().model_dump(), indent=2))
    # print(json.dumps(Config().get_sites(), indent=2))
    # print(json.dumps(Config().get_users(), indent=2))
    for conf in Config().get_users():
        if conf.picture:
            img = Image.open(io.BytesIO(conf.picture))
            plt.imshow(img)
            plt.axis("off")  # Hide axes
            plt.show()
        else:
            print(f"no picture for user '{conf.name}'")
        print(json.dumps(conf.model_dump(), indent=2))
    # print(json.dumps(Config().get_user("arie"), indent=2))
    # print(json.dumps([s.model_dump() for s in Config().sites]))
    # print(json.dumps(Config().get_unit("mast00").model_dump(), indent=1))
