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


class ExcludeRegionMode(StrEnum):
    """Whether PHD2 guide-star selection avoids the configured region."""

    OFF = "off"  # no exclusion region: PHD2 selects anywhere it is otherwise allowed
    FIXED = "fixed"  # the configured rectangle (unbinned camera pixels)


class ExcludeRegionConfig(BaseModel):
    """Persisted configuration for the PHD2 guide-star exclusion region.

    The region (unbinned camera pixels) is excluded from PHD2 guide-star
    auto-selection, so guiding locks only on stars the FCU fold mirror will not
    occult and the mirror can be inserted after guiding is locked.

    ``mode`` names the outcome directly, as in :class:`LimitFrameConfig`:

    - ``off`` (default) -- no exclusion region; it is reset before guiding. The
      only safe default: unlike the limit frame there is no derived fallback
      rectangle, and the mirror shadow must be measured per unit before the
      feature can do anything but suppress guide stars for no reason.
    - ``fixed`` -- the rectangle below. Requires a complete rectangle.

    One deliberate asymmetry with ``limit_frame``: a rectangle configured under
    ``off`` is legal here rather than a contradiction. The shadow-measurement tool
    writes each unit's band (with its derivation record) as soon as it is measured
    and the region is switched on later, per unit; rejecting the pair would force
    an operator to delete a measurement in order to disable the feature, and would
    make "measured but not yet enabled" inexpressible.

    Requires the ``set_exclude_region`` PHD2 API (MAST build
    ``2.6.14dev1mastbuild4`` or later).
    """

    mode: ExcludeRegionMode = Field(
        default=ExcludeRegionMode.OFF,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "select",
                "options": ["off", "fixed"],
                "label": "Exclusion region",
                "tooltip": "off: no exclusion region; fixed: exclude the rectangle below "
                "(the fold-mirror shadow) from PHD2 guide-star selection",
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

    @model_validator(mode="after")
    def _rect_matches_mode(self):
        if self.mode is ExcludeRegionMode.FIXED and (self.width <= 0 or self.height <= 0):
            raise ValueError("phd2.exclude_region: mode 'fixed' requires a complete rectangle (positive width and height)")
        return self

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
