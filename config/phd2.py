from pydantic import BaseModel, Field

from common.config.identification import UserCapabilities


class PHD2SettleConfig(BaseModel):
    """Configuration for PHD2 settle settings."""

    pixels: int
    time: int
    timeout: int


class LimitFrameConfig(BaseModel):
    """Persisted configuration for the PHD2 limit frame (guide-star selection area).

    When ``enabled`` is False the limit frame is reset (PHD2 uses the full frame).
    When ``enabled`` is True and a rectangle is given, it is used verbatim (unbinned
    camera pixels).  When ``enabled`` is True and no rectangle is given (``width`` or
    ``height`` is 0), the guiding ROI derived from ``guiding.rois`` (fiber position
    and margins) is used, as before.
    """

    enabled: bool = Field(
        default=True,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "checkbox",
                "label": "Use limit frame",
                "tooltip": "Confine PHD2 guide-star selection to the configured frame",
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
                "tooltip": "Limit frame origin X (unbinned camera pixels)",
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
                "tooltip": "Limit frame origin Y (unbinned camera pixels)",
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
                "tooltip": "Limit frame width (unbinned camera pixels, 0 means not configured)",
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
                "tooltip": "Limit frame height (unbinned camera pixels, 0 means not configured)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )

    @property
    def has_roi(self) -> bool:
        return self.width > 0 and self.height > 0


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
