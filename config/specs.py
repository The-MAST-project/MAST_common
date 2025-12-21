from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, model_validator

from common.deep import deep_dict_update

from .chiller import ChillerConfig
from .greateyes import GreateyesConfig
from .newton import NewtonSettingsConfig
from .power import OutletConfig, PowerConfig, PowerSwitchConfig
from .stage import SpecStagesConfig


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

        valid_filter_names = ["1", "2", "3", "4", "5", "6", "default", "Empty"]
        for key in list(self.filters.keys()):
            if key not in valid_filter_names:
                raise ValueError(f"filter name {key} not in {valid_filter_names} ")
        if "default" not in self.filters:
            self.filters["default"] = "Empty"
        elif self.filters["default"] not in valid_filter_names:
            raise ValueError(
                f"Default filter '{self.filters['default']}' must be one of the defined filters."
            )
        return self


class GratingConfig(BaseModel):
    focus_position: int  # for focusing the HighSpec camera


class DeepspecConfig(BaseModel):
    dict[Literal["G", "I", "U", "R", "common"], GreateyesConfig]


class HighspecConfig(BaseModel):
    """Configuration for the Newton camera."""

    power: OutletConfig
    settings: NewtonSettingsConfig
    camera: str  # which camera to use, e.g. 'qhy600' or 'newton'
    valid_cameras: list[str]  # list of valid camera names


class ServerConfig(BaseModel):
    """Configuration for the server."""

    host: str | None = None  # IP address on which the server will listen
    port: int = 8002

    @model_validator(mode="after")
    def validate_server_config(self):
        if self.host is None:
            self.host = "0.0.0.0"  # Default to all interfaces
        return self


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
        for name in list(self.wheels.keys()):
            if name not in valid_wheel_names:
                raise ValueError(
                    f"validate_specs_config: invalid wheel name '{name}', '{valid_wheel_names=}'"
                )

        # gratings
        valid_grating_names = ["Halpha", "Mg", "Ca", "Future"]
        for name in list(self.gratings.keys()):
            if name not in valid_grating_names:
                raise ValueError(
                    f"validate_specs_config: invalid grating name '{name}', {valid_grating_names=}"
                )

        valid_lamp_names = valid_wheel_names
        for name in list(self.lamps.keys()):
            if name not in valid_lamp_names:
                raise ValueError(
                    f"validate_specs_config: invalid lamp name '{name}', '{valid_lamp_names=}'"
                )

        valid_deepspec_camera_names = ["G", "I", "U", "R", "common"]
        for name in list(self.deepspec.keys()):
            if name not in valid_deepspec_camera_names:
                raise ValueError(
                    f"validate_specs_config: invalid deepspec camera name '{name}', {valid_deepspec_camera_names=}"
                )

        if "common" not in self.deepspec:
            raise ValueError(
                "validate_specs_config: 'common' deepspec configuration not found"
            )

        common_cfg = self.deepspec.get("common")
        if not common_cfg:
            raise ValueError(
                "validate_specs_config: 'common' deepspec configuration is None"
            )

        if any([common_cfg.network, common_cfg.power, common_cfg.device]):
            raise ValueError(
                "validate_specs_config: 'common' deepspec configuration should not have network, power or device set"
            )
        if not common_cfg.settings:
            raise ValueError(
                "validate_specs_config: 'common' deepspec configuration must have settings set"
            )

        for band in self.deepspec:
            if band == "common":
                continue

            band_cfg = self.deepspec[band]
            if not band_cfg:
                raise ValueError(
                    f"validate_specs_config: '{band}' deepspec configuration not found"
                )

            if not band_cfg.network or not band_cfg.power or band_cfg.device is None:
                raise ValueError(
                    f"validate_specs_config: '{band}' deepspec configuration must have network, power and device set"
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
