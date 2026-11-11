"""The `safety` collection: which weather sensors guard the enclosure, and their limits.

One document per site::

    {
        "site": "ns",
        "sensors": {
            "wind_speed": {"enabled": true, "max": 25.0, "settling": 60},
            "outside_humidity": {"enabled": true, "max": 85.0, "settling": 300},
            "rain_rate": {"enabled": true, "max": 0.0, "settling": 900},
        },
        "sun_elevation": {"enabled": true},
    }

`sensors` are keyed by the weather service's own `davis` sensor names. `settling` is the
stabilization window in seconds: the sensor stays not safe until it has been observed, without
gaps and within limits, for that long. Without it the newest reading alone decides.

`sun_elevation` is computed, not read, and carries its own `dawn` / `dusk` limits in degrees: the
sun above the limit is not safe (`dawn` while it rises, `dusk` while it sets). They are deliberately
not taken from the site's `location.sun_limits`, which defaults to -18 when absent.

Unknown keys are refused: a mistyped `setling` would otherwise be silently ignored, and the
sensor would run with no window at all.
"""

from pydantic import BaseModel, ConfigDict, model_validator


class SensorLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    min: float | None = None
    max: float | None = None
    settling: float | None = None  # seconds

    @model_validator(mode="after")
    def _check(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min}) is above max ({self.max})")
        if self.settling is not None and self.settling < 0:
            raise ValueError(f"settling ({self.settling}) is negative")
        return self


class SunElevationLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    dawn: float  # degrees
    dusk: float  # degrees


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    site: str
    sensors: dict[str, SensorLimits] = {}
    sun_elevation: SunElevationLimits | None = None
