from typing import Literal

from pydantic import BaseModel, model_validator

from .network import NetworkConfig
from .power import OutletConfig
from .utils import flatten, literal_values

HighspecPresets = Literal["Ca", "Halpha", "Mg", "Future"]
DeepspecPresets = Literal["deepspec", "highspec"]
StagePresetNames = HighspecPresets | DeepspecPresets


class StagePresets(BaseModel):
    """Configuration for stage preset positions."""

    sky: int
    spec: int


class StageConfig(BaseModel):
    """Configuration for the telescope stage."""

    presets: StagePresets
    close_enough: int


class SpecStageConfig(BaseModel):
    """Configuration for the spectrograph stages"""

    peripheral: str
    presets: dict[str, int]
    startup_preset: str | None = None
    shutdown_preset: str | None = None

    @model_validator(mode="after")
    def validate_spec_stage_config(self):
        for name in flatten(
            [list(self.presets.keys()), self.startup_preset, self.shutdown_preset]
        ):
            if name is not None and name not in literal_values(StagePresetNames):
                raise ValueError(
                    f"validate_spec_stage_config: {name=} not in {StagePresetNames}"
                )
        return self


class SpecStageControllerConfig(BaseModel):
    """Configuration for the spectrograph stages controller."""

    network: NetworkConfig
    power: OutletConfig


class FiberStageConfig(BaseModel):
    peripheral: str
    presets: dict[str, int]
    startup_preset: str | None = None
    shutdown_preset: str | None = None

    @model_validator(mode="after")
    def validate_spec_stage_config(self):
        for name in flatten(
            [
                list(self.presets.keys()),
                self.startup_preset,
                self.shutdown_preset,
            ]
        ):
            if name is not None and name not in literal_values(StagePresetNames):
                raise ValueError(
                    f"validate_spec_stage_config: {name=} not in {StagePresetNames}"
                )
        return self


class SpecStagesConfig(BaseModel):
    """Configuration for the spectrograph stages controller."""

    controller: SpecStageControllerConfig
    fiber: FiberStageConfig
    disperser: SpecStageConfig
    focusing: SpecStageConfig
