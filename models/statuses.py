from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from common.activities import ActivitiesVerbal
from common.dlipowerswitch import PowerStatus, PowerSwitchStatus
from common.interfaces.components import ComponentStatus
from common.interfaces.imager import ImagerStatus


# ASCOM stuff
class AscomDriverInfoModel(BaseModel):
    name: str
    description: str
    version: str
    connected: bool = False


class AscomStatus(BaseModel):
    ascom: AscomDriverInfoModel


# Covers stuff
# https://ascom-standards.org/Help/Developer/html/T_ASCOM_DeviceInterface_CoverStatus.htm
class CoversState(Enum):
    NotPresent = 0
    Closed = 1
    Moving = 2
    Open = 3
    Unknown = 4
    Error = 5


class CoverStatus(PowerStatus, AscomStatus, ComponentStatus):
    target_verbal: str | None = None
    state: CoversState | None = None
    state_verbal: str | None = None
    date: str | None = None


# Focuser stuff
class FocuserStatus(PowerStatus, AscomStatus, ComponentStatus):
    lower_limit: int | None = None
    upper_limit: int | None = None
    known_as_good_position: int | None = None
    position: int | None = None
    target: int | None = None
    target_verbal: str | None = None
    moving: bool = False
    date: str | None = None


# Guider stuff
class SkyQualityStatus(BaseModel):
    score: float | None = None
    state: str | None = None
    latest_update: str | None = None


class PHD2GuiderStatus(BaseModel):
    identifier: str | None = None
    is_guiding: bool = False
    is_settling: bool = False
    app_state: str | None = None
    avg_dist: float | None = None
    sky_quality: SkyQualityStatus | None = None


class GuiderStatus(BaseModel):
    activities: int | None = None
    activities_verbal: ActivitiesVerbal = None
    backend: PHD2GuiderStatus | None = None


# Stage stuff
class StageStatus(PowerStatus, ComponentStatus):
    info: dict | None = None
    presets: dict | None = None
    position: int | None = None
    at_preset: str | None = None
    target: int | None = None
    target_verbal: str | None = None
    date: str | None = None


# Mount stuff
class SpiralSettings(BaseModel):
    x: float
    y: float
    x_step_arcsec: float
    y_step_arcsec: float


class MountStatus(PowerStatus, AscomStatus, ComponentStatus):
    errors: list[str] | None = None
    target_verbal: str | None = None
    tracking: bool = False
    slewing: bool = False
    axis0_enabled: bool = False
    axis1_enabled: bool = False
    ra_j2000_hours: float | None = None
    dec_j2000_degs: float | None = None
    ha_hours: float | None = None
    lmst_hours: float | None = None
    fans: bool = False
    spiral: SpiralSettings | None = None
    date: str | None = None


# PHD2 Imager status
class PHD2ImagerStatus(BaseModel):
    identifier: str | None = None
    name: str = "phd2"
    activities: int = 0
    activities_verbal: ActivitiesVerbal = None
    operational: bool = False
    why_not_operational: list[str] = []
    connected: bool = False


class BaseStatus(BaseModel):
    """Base class for unit status."""

    powered: bool
    detected: bool
    operational: bool
    why_not_operational: list[str] | None = None


class ShortStatus(BaseStatus):
    """Unit status returned by the controller when it cannot communicate with the unit"""

    type: Literal["short"] = "short"


class NotPoweredStatus(ShortStatus):
    def model_post_init(self, __context: Any) -> ShortStatus:
        return ShortStatus(
            powered=False,
            detected=False,
            operational=False,
            why_not_operational=["Not powered"],
        )


class NotDetectedStatus(ShortStatus):
    def model_post_init(self, __context: Any) -> ShortStatus:
        return ShortStatus(
            powered=True,
            detected=False,
            operational=False,
            why_not_operational=["Not detected"],
        )


class NotOperationalStatus(ShortStatus):
    def model_post_init(self, __context: Any) -> ShortStatus:
        if "reasons" in __context:
            reasons = __context["reasons"]
        else:
            reasons = ["Not operational"]

        return ShortStatus(
            powered=True,
            detected=True,
            operational=False,
            why_not_operational=reasons,
        )


class FullUnitStatus(BaseStatus, ComponentStatus, PowerStatus):
    """Full unit status with all components, returned from the unit itself."""

    type: Literal["full"] = "full"
    id: int
    guiding: bool = False
    autofocusing: bool = False
    power_switch: PowerSwitchStatus | None = None
    mount: MountStatus | None = None
    imager: ImagerStatus | PHD2ImagerStatus | None = None
    covers: CoverStatus | None = None
    focuser: FocuserStatus | None = None
    stage: StageStatus | None = None
    guider: GuiderStatus | None = None
    errors: list[str] | None = None
    autofocus: dict | None = None
    corrections: list | None = None
    date: str | None = None
    powered: bool = True


# Using Annotated with Discriminator (Pydantic v2 recommended approach)
UnitStatus = Annotated[ShortStatus | FullUnitStatus, Field(discriminator="type")]


# Example usage in an API response model:
class UnitStatusResponse(BaseModel):
    """API response containing unit status."""

    unit_name: str
    timestamp: str
    status: UnitStatus  # This is the discriminated union


class DeepSpecStatus(BaseModel):
    type: Literal["deepspec"] = "deepspec"


class HighSpecStatus(BaseModel):
    type: Literal["highspec"] = "highspec"


class ControllerStatus(BaseModel):
    """Status of the controller."""

    activities: int
    activities_verbal: ActivitiesVerbal


class SiteStatus(BaseModel):
    """Status of a controlled site."""

    controller: ControllerStatus | None = None
    units: dict[str, UnitStatus] | None = None
    deepspec: DeepSpecStatus | None = None
    highspec: HighSpecStatus | None = None


class SitesStatus(BaseModel):
    """API response containing status of all controlled sites."""

    timestamp: str
    sites: dict[str, SiteStatus]


# # Example usage:
# if __name__ == "__main__":
#     import json

#     # Short status
#     short = ShortUnitStatus(powered=True, detected=True, operational=True)
#     print(json.dumps(short.model_dump(), indent=2))

#     # Full status
#     full = FullUnitStatus(
#         powered=True,
#         detected=True,
#         operational=False,
#         why_not_operational=["Mount not responding"],
#         activities_verbal=["Parking"],
#         mount_connected=False,
#         camera_connected=True
#     )
#     print(json.dumps(full.model_dump(), indent=2))

#     # Response with short status
#     response1 = UnitStatusResponse(
#         unit_name="mastw",
#         timestamp="2024-12-03T18:00:00Z",
#         status=short
#     )
#     print(json.dumps(response1.model_dump(), indent=2))

#     # Response with full status
#     response2 = UnitStatusResponse(
#         unit_name="mast01",
#         timestamp="2024-12-03T18:00:00Z",
#         status=full
#     )
#     print(json.dumps(response2.model_dump(), indent=2))
