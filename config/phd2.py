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
        if self.pad_px is not None and self.derived_from_pad_px is not None and self.pad_px != self.derived_from_pad_px:
            return f"pad_px={self.pad_px} but the rectangle was derived with pad_px={self.derived_from_pad_px}"
        return None


class LockValidityConfig(BaseModel):
    """Thresholds for the guide-lock validity supervisor (`science.lock_validity`).

    Config lives here rather than beside the component so a unit can be retuned
    from the controller DB without a deployment -- which is what the campaign
    wanted on the night and could not have.
    """

    #: Frames before the session scale is trusted. Eight is ~113 s at the measured
    #: 9.58 s cadence. Below it the 2026-09-08 replay produces a false alarm; above
    #: it nothing improves, and the closest sound frame sits 1.34x clear of the cut.
    warmup_frames: int = Field(default=8, ge=3, le=200)

    #: How many masses the scale is taken over. Sixty is ~10 minutes -- long enough
    #: to be stable, short enough to follow a field change after a re-guide.
    scale_window_frames: int = Field(default=60, ge=10, le=1000)

    #: Below this fraction of the session scale the lock is not that object. The
    #: empty band on 2026-09-08 runs 0.021 to 0.085, so 0.05 sits in the middle of
    #: a region containing no frames at all.
    artifact_mass_fraction: float = Field(default=0.05, gt=0.0, lt=1.0)

    #: Stateless test. A real star's peak stands clear of the sky; an artifact's
    #: peak *is* the sky. Expressed in sigma so it is free of the exposure, the
    #: gain and the moon -- an absolute ADU threshold is none of those things.
    min_peak_sigma_over_background: float = Field(default=15.0, gt=0)

    #: Stateless test, second half: how concentrated the light is, normalised by
    #: the seeing disc. Raw `mass / peak` is **not** usable -- it correlates with
    #: HFD at r = 0.90 over 2026-09-08, because mass grows with the aperture the
    #: star fills, so it is an HFD test wearing a disguise. Real locks at HFD 3-4
    #: median 9.2 on it, which a floor set for a 6.7 px night would condemn
    #: wholesale on a sharp one. Dividing by HFD^2 drops that to r = 0.54.
    #:
    #: Set **above** the artifact range rather than between the populations,
    #: because the two halves must agree before the stateless test objects: this
    #: one corroborates, `min_peak_sigma_over_background` discriminates.
    min_mass_over_peak_hfd2: float = Field(default=0.35, gt=0)

    #: Frames a verdict must persist before the state changes. One frame of bad
    #: seeing should not flip the state, and one good frame should not clear it.
    hysteresis_frames: int = Field(default=2, ge=1, le=20)

    #: Without the PHD2 build that reports peak and background, run the session
    #: test alone rather than refusing to run. False is the honest default: half a
    #: check is better than none, and the log says which half is missing.
    require_background: bool = False

    #: **Does reaching NotAStar stop the guiding?** Detection and action are
    #: separate switches on purpose. The supervisor is meant to run for a night
    #: reporting only, so the state can be read against what actually happened
    #: before anything acts on it -- and so the action can be withdrawn without
    #: losing the signal if it proves too eager.
    end_guiding_on_not_a_star: bool = False


class PHD2Config(BaseModel):
    profile: str
    settle: PHD2SettleConfig
    validation_interval: float
    limit_frame: LimitFrameConfig = Field(default_factory=LimitFrameConfig)
    exclude_region: ExcludeRegionConfig = Field(default_factory=ExcludeRegionConfig)
    lock_validity: LockValidityConfig = Field(default_factory=LockValidityConfig)
