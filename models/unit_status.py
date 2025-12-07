from typing import Annotated, Literal

from pydantic import BaseModel, Field

from common.dlipowerswitch import PowerStatus, PowerSwitchStatus
from common.interfaces.components import ComponentStatus
from common.interfaces.imager import ImagerStatus
from covers import CoverStatus
from focuser import FocuserStatus
from guiding import GuiderStatus
from mount import MountStatus
from phd2.phd2 import PHD2ImagerStatus
from stage import StageStatus


class BaseUnitStatus(BaseModel):
    """Base class for unit status."""
    powered: bool
    detected: bool
    operational: bool

class ShortUnitStatus(BaseUnitStatus):
    """Unit status returned by the controller when it cannot communicate with the unit"""
    type: Literal["short"] = "short"

class FullUnitStatus(BaseUnitStatus, ComponentStatus, PowerStatus):
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
UnitStatus = Annotated[
    ShortUnitStatus | FullUnitStatus,
    Field(discriminator="type")
]


# Example usage in an API response model:
class UnitStatusResponse(BaseModel):
    """API response containing unit status."""
    unit_name: str
    timestamp: str
    status: UnitStatus  # This is the discriminated union


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
