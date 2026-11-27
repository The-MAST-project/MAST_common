from pydantic import BaseModel, Field, field_validator, model_validator

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


class SolvingConfig(BaseModel):
    method: str
    valid_methods: list[str]


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
        # The in-house 'mast' guider tracks its fixed star set on the 'zwo' imager's
        # full-frame binning-2 frames, and that imaging path (the acquirer's
        # _mast_full_frame_binned2) exists for it -- the two are one stack, not independent
        # choices. A config that pairs one with a different partner can never guide:
        #   - imager 'zwo' + guider != 'mast'  -> the zwo stack with a guider that won't drive it;
        #   - guider 'mast' + imager != 'zwo'  -> the mast guider with no zwo frames to track.
        # Reject it here, at parse time, so a bad *live* edit on config reload is caught too,
        # not only a bad config at startup (app.py's verify_imager_guider_coherence catches
        # that). The literals mirror ImagerTypes.Zwo / GuiderTypes.Mast -- the enums are not
        # imported here to keep this low-level config model free of the imaging stack.
        imager_is_zwo = self.imager.imager_type == "zwo"
        guider_is_mast = self.guider.method == "mast"
        if imager_is_zwo != guider_is_mast:
            raise ValueError(
                f"incoherent imager/guider configuration: {self.imager.imager_type=}, "
                f"{self.guider.method=}. The 'zwo' imager and the 'mast' guider are one "
                "stack -- configure both together, or neither."
            )
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
