from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from common.asi import ASI_294MM_SUPPORTED_BINNINGS_LITERAL
from common.opmode import OpMode

from .calibration import CalibrationConfig
from .covers import CoversConfig
from .focuser import FocuserConfig
from .imager import ImagerConfig
from .mount import MountConfig
from .opmode import opmode_field
from .phd2 import PHD2Config
from .power import PowerSwitchConfig
from .rois import RoisConfig
from .stage import StageConfig
from .supervisor import SupervisorConfig


class ToleranceConfig(BaseModel):
    """Configuration for the acquisition tolerances, in arcsec on the sky.

    The unit judges offsets in plain RA (the RA coordinate difference, as the mount offset
    takes it), so it applies ``ra_arcsec`` as ``ra_arcsec / cos(dec)`` of the target.
    """

    ra_arcsec: float
    dec_arcsec: float


class AcquisitionConfig(BaseModel):
    """Configuration for the acquisition settings."""

    exposure: float
    binning: ASI_294MM_SUPPORTED_BINNINGS_LITERAL
    tolerance: ToleranceConfig
    tries: int
    gain: int
    rois: RoisConfig


class GuidingConfig(BaseModel):
    """Configuration for guiding settings."""

    exposure: float
    binning: ASI_294MM_SUPPORTED_BINNINGS_LITERAL
    tolerance: ToleranceConfig
    gain: int
    min_ra_correction_arcsec: float
    min_dec_correction_arcsec: float
    cadence_seconds: int
    rois: RoisConfig


class GuiderConfig(BaseModel):
    method: str
    valid_methods: list[str]


class SourceSelectionConfig(BaseModel):
    """How the solver chooses the sources it hands to astrometry.net (MAST_unit `solvers/source_selection.py`).

    `mode`: ``off`` -- solve the image as before; ``shadow_only`` -- detect the fold-mirror shadow
    and darken its band (umbra and penumbra), then solve the image; ``full`` -- select the sources
    and solve that list, falling back to the image solve if that fails.

    Every other field is a parameter of the selection. ``None`` means the unit's own default, so the
    defaults are spelled once, in `SelectionParams`, and a unit overrides only what it has measured.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["off", "shadow_only", "full"] = "off"
    nsigma: float | None = None
    npixels: int | None = None
    box_size: int | None = None
    seeing_fwhm_px: float | None = None
    default_fwhm_px: float | None = None
    max_shadow_deficit: float | None = None
    sat_frac: float | None = None
    full_scale: float | None = None
    min_area: int | None = None
    sharp_fwhm_frac: float | None = None
    min_sep_fwhm: float | None = None
    max_fwhm_ratio: float | None = None
    blend_flux_ratio: float | None = None
    neighbour_min_snr: float | None = None
    blend_ignores_hot: bool | None = None
    edge_margin_sigmas: float | None = None
    edge_margin_extra_px: float | None = None
    min_snr: float | None = None
    relaxed_min_snr: float | None = None
    max_centroid_shift_fwhm: float | None = None
    n_target: int | None = None
    n_max: int | None = None
    coma_px: float | None = None
    radius_growth: float | None = None
    grid: tuple[int, int] | None = None
    fail_below: int | None = None
    geometric_low_coma_fraction: float | None = None
    #: replaces the shadow-deficit test with a plain exclusion half-width around the band, pixels
    exclusion_half_width: float | None = None


class SolvingConfig(BaseModel):
    method: str
    valid_methods: list[str]
    #: absent or ``mode = "off"``: the solver behaves as it always has
    source_selection: SourceSelectionConfig | None = None


class AutofocusConfig(BaseModel):
    """Configuration for autofocus settings."""

    exposure: float
    binning: ASI_294MM_SUPPORTED_BINNINGS_LITERAL
    rois: RoisConfig | None = None
    images: int
    spacing: int
    max_tolerance: int
    max_tries: int


class UnitConfig(BaseModel):
    """Complete configuration for a MAST unit.

    This is the top-level configuration class that contains all settings
    needed to configure and operate a MAST unit.
    """

    name: str
    power_switch: PowerSwitchConfig
    imager: ImagerConfig
    stage: StageConfig
    focuser: FocuserConfig
    covers: CoversConfig
    mount: MountConfig
    phd2: PHD2Config
    acquisition: AcquisitionConfig
    solving: SolvingConfig
    guiding: GuidingConfig
    autofocus: AutofocusConfig
    guider: GuiderConfig
    calibration: CalibrationConfig | None = None
    opmode: OpMode = opmode_field()
    supervisor: SupervisorConfig = Field(default_factory=SupervisorConfig.for_unit)
    # Whether a shutdown also powers the machine's components down. In the config database
    # for now (units.common, overridable per unit / the specs document) so the behaviour can
    # be tried both ways; to be hard-coded once settled. Default False: shutdown, then a
    # separate powerdown. No UI metadata yet -- no config field has a boolean widget.
    # Design: opmode-design section 4a.
    power_down_on_shutdown: bool = False
    # Minutes of continuous idleness (STARTED_UP, nothing busy) after which the unit shuts itself
    # down. 0 disables, and is the default; otherwise at least 5, so a mistyped value cannot
    # shut a unit down a minute after its last command. Read live. Design: mast-claude-config
    # plans/unit_idleness_design.md.
    minutes_to_autoshutdown_on_idle: int = Field(
        default=0,
        ge=0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "min",
                "label": "Auto-shutdown When Idle",
                "tooltip": "Minutes idle before the unit shuts itself down; 0 disables, otherwise at least 5",
            },
        },
    )

    # Seconds an `autostartup` route waits for the unit's startup to complete before it answers
    # with the unit's `why_not_operational`. The startup is left running either way. 120 is 2.5x
    # the slowest startup on record (94 startups on mast00-mast04, 2026-10/11: median 34 s,
    # max 48 s). Read live.
    seconds_to_autostartup: int = Field(
        default=120,
        gt=0,
        json_schema_extra={
            "ui": {
                "editable": True,
                "widget": "number",
                "unit": "s",
                "label": "Autostartup Wait",
                "tooltip": "Seconds a route that starts the unit up waits for the startup to complete",
            },
        },
    )

    @field_validator("minutes_to_autoshutdown_on_idle")
    @classmethod
    def _zero_or_at_least_five(cls, minutes: int) -> int:
        if 0 < minutes < 5:
            raise ValueError(f"minutes_to_autoshutdown_on_idle={minutes}: use 0 to disable, or at least 5")
        return minutes

    @model_validator(mode="after")
    def validate_unit_config(self):
        # conditions = [self.guider.method == "phd2", self.imager.imager_type == "phd2"]

        # if any(conditions) and not all(conditions):
        #     raise ValueError(
        #         f"if any of {self.imager.imager_type=} or {self.guider.method=} is 'phd2', then BOTH must be 'phd2'"
        #     )
        # logger = get_logger(__name__)
        # logger.debug(f"Validated UnitConfig for unit '{self.name}', focuser: '{self.focuser}'")
        return self

    # NOTE: a ``focus_seed_position()`` helper used to live here, preferring
    # ``calibration.focuser.best_position`` over the operational
    # ``focuser.known_as_good_position``.  It was removed deliberately when the
    # calibration flow was decoupled from the ps3cli path: it had no callers,
    # but had one been added, writing a calibration product would have silently
    # changed operational focus seeding through the back door.  The two paths
    # now share no state -- ps3cli owns ``focuser.known_as_good_position``,
    # calibration owns ``calibration.products.focuser`` -- and each seeds only
    # from its own.
