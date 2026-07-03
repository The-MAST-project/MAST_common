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
                raise ValueError("phd2.limit_frame: mode 'fixed' requires a complete rectangle (positive width and height)")
        elif any((self.x, self.y, self.width, self.height)):
            raise ValueError(
                f"phd2.limit_frame: a rectangle is configured but mode is "
                f"'{self.mode}' -- the rectangle applies only to mode 'fixed'"
            )
        return self


class ExcludeRegionConfig(BaseModel):
    """Persisted configuration for the PHD2 guide-star exclusion region.

    The region (unbinned camera pixels) is excluded from PHD2 guide-star
    auto-selection, so guiding locks only on stars the FCU fold mirror will not
    occult and the mirror can be inserted after guiding is locked.  Disabled by
    default: the rectangle is per-unit geometry (the mirror shadow plus a safety
    margin) and must be measured before enabling.  Requires the ``set_exclude_region``
    PHD2 API (MAST build 2.6.14dev1mast04 or later).
    """

    enabled: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "checkbox",
                "label": "Use exclusion region",
                "tooltip": "Exclude the configured region (fold-mirror shadow) from PHD2 guide-star selection",
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
                "tooltip": "Exclusion region origin X (unbinned camera pixels)",
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
                "tooltip": "Exclusion region origin Y (unbinned camera pixels)",
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
                "tooltip": "Exclusion region width (unbinned camera pixels, 0 means not configured)",
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
                "tooltip": "Exclusion region height (unbinned camera pixels, 0 means not configured)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )

    @property
    def has_roi(self) -> bool:
        return self.width > 0 and self.height > 0


class PHD2Config(BaseModel):
    profile: str
    settle: PHD2SettleConfig
    validation_interval: float
    limit_frame: LimitFrameConfig = Field(default_factory=LimitFrameConfig)
    exclude_region: ExcludeRegionConfig = Field(default_factory=ExcludeRegionConfig)
