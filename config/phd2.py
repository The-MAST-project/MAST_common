from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from common.config.identification import UserCapabilities


class PHD2SettleConfig(BaseModel):
    """Configuration for PHD2 settle settings."""

    pixels: int
    time: int
    timeout: int


class LimitFrameMode(StrEnum):
    """Where PHD2 guide-star selection may roam when guiding starts."""

    DERIVED = "derived"  # limit frame from the fiber/margin-derived guiding ROI (deployed behavior)
    FULL_FRAME = "full_frame"  # no limit frame: full-sensor star selection
    FIXED = "fixed"  # the configured rectangle (unbinned camera pixels)


class LimitFrameConfig(BaseModel):
    """Persisted configuration for the PHD2 limit frame (guide-star selection area).

    ``mode`` names the outcome directly:

    - ``derived`` (default) -- the guiding ROI derived from ``guiding.rois`` (fiber
      position and margins), exactly the pre-config behavior.
    - ``full_frame`` -- the limit frame is reset; PHD2 selects guide stars anywhere
      on the sensor.
    - ``fixed`` -- the rectangle below (unbinned camera pixels). Requires a complete
      rectangle; a rectangle configured under any other mode is rejected as a
      contradiction rather than silently ignored.
    """

    mode: LimitFrameMode = Field(
        default=LimitFrameMode.DERIVED,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "select",
                "options": ["derived", "full_frame", "fixed"],
                "label": "Limit frame",
                "tooltip": "derived: from fiber position/margins; full_frame: no limit "
                "frame (full-sensor star selection); fixed: the rectangle below",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )
    x: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "pixels",
                "label": "X",
                "tooltip": "Limit frame origin X (unbinned camera pixels; mode 'fixed' only)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )
    y: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "pixels",
                "label": "Y",
                "tooltip": "Limit frame origin Y (unbinned camera pixels; mode 'fixed' only)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )
    width: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "pixels",
                "label": "Width",
                "tooltip": "Limit frame width (unbinned camera pixels; mode 'fixed' only)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )
    height: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "pixels",
                "label": "Height",
                "tooltip": "Limit frame height (unbinned camera pixels; mode 'fixed' only)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )

    @model_validator(mode="after")
    def _rect_matches_mode(self):
        if self.mode is LimitFrameMode.FIXED:
            if self.width <= 0 or self.height <= 0:
                raise ValueError(
                    "phd2.limit_frame: mode 'fixed' requires a complete rectangle "
                    "(positive width and height)"
                )
        elif any((self.x, self.y, self.width, self.height)):
            raise ValueError(
                f"phd2.limit_frame: a rectangle is configured but mode is "
                f"'{self.mode}' -- the rectangle applies only to mode 'fixed'"
            )
        return self


class PHD2Config(BaseModel):
    profile: str
    settle: PHD2SettleConfig
    validation_interval: float
    limit_frame: LimitFrameConfig = Field(default_factory=LimitFrameConfig)
