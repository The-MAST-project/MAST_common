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
    depth: float | None = Field(
        default=None,
        gt=0.0,
        le=1.0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "label": "Penumbra depth",
                "tooltip": "Shadow-depth fraction at which the exclusion boundary is drawn (per unit)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )
    pad_px: int | None = Field(
        default=None,
        ge=0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "pixels",
                "label": "Pad",
                "tooltip": "Safety margin added around the measured shadow band (unbinned camera pixels)",
            },
            "required_capabilities": [UserCapabilities.CAN_CHANGE_CONFIGURATION.value],
        },
    )
    derived_from_depth: float | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "editable": False,
                "label": "Rect derived at depth",
                "tooltip": "Depth the stored rectangle was derived at - written by the shadow-measurement tool only",
            },
        },
    )
    derived_from_pad_px: int | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "editable": False,
                "label": "Rect derived with pad",
                "tooltip": "Pad the stored rectangle was derived with - written by the shadow-measurement tool only",
            },
        },
    )

    @property
    def has_roi(self) -> bool:
        return self.width > 0 and self.height > 0

    def stale_derivation(self) -> str | None:
        """How the stored rectangle disagrees with the depth/pad knobs, or None.

        The rectangle is a cached derived value: the shadow-measurement tool is
        its sole writer and records the depth/pad it derived from.  A hand-edited
        knob that disagrees with that record means the rectangle is stale and
        must not be trusted for guiding.
        """
        if self.depth is None and self.pad_px is None:
            return None
        if self.has_roi and self.derived_from_depth is None and self.derived_from_pad_px is None:
            return "depth/pad_px are set but the rectangle carries no derivation record"
        if (
            self.depth is not None
            and self.derived_from_depth is not None
            and abs(self.depth - self.derived_from_depth) > 1e-9
        ):
            return f"depth={self.depth} but the rectangle was derived at depth={self.derived_from_depth}"
        if (
            self.pad_px is not None
            and self.derived_from_pad_px is not None
            and self.pad_px != self.derived_from_pad_px
        ):
            return f"pad_px={self.pad_px} but the rectangle was derived with pad_px={self.derived_from_pad_px}"
        return None


class PHD2Config(BaseModel):
    profile: str
    settle: PHD2SettleConfig
    validation_interval: float
    limit_frame: LimitFrameConfig = Field(default_factory=LimitFrameConfig)
    exclude_region: ExcludeRegionConfig = Field(default_factory=ExcludeRegionConfig)
