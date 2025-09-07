from typing import Literal

from pydantic import BaseModel

from .network import NetworkConfig


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
