"""Per-unit calibration state persisted in the unit config DB.

These values are *written by the unit's calibration routines* (not user-edited):
the optical-center finder writes :class:`OpticalCenterCalibration`, autofocus and
(later) the pick-off stage-geometry / thermal focus-seed steps read and extend
this block.  Each carries provenance + quality so a bad calibration can be
rejected, and a shared ``mechanical_epoch`` groups the geometric calibrations so
servicing the optics invalidates them together.

Design reference: unit self-calibration design, sections 3 (outputs), 11
(storage).  The optical-center -> autofocus coupling: coma is radial about the
optical center and grows with field radius, so the *low-coma* region autofocus
must restrict itself to is a disk centered on the optical center (the opposite of
the optical-center finder, which prefers the coma-heavy margins).
"""

from pydantic import BaseModel, Field

from common.asi import ASI_294MM_SUPPORTED_BINNINGS_LITERAL

# ---------------------------------------------------------------------------
# Settings (inputs) -- what the phases READ.
#
# Deliberately separated from the products below, which the phases WRITE.  The
# original design put them on the same keys (``calibration.optical_center.
# number_of_frames`` alongside the measured centre), which cannot work: the
# product is ``None`` until the first successful run, so a first calibration
# would have nowhere to read its own inputs from.  Settings therefore live under
# ``calibration.settings.*`` and products stay at ``calibration.{focuser,
# optical_center,stage}`` -- already-written product paths are unaffected.
#
# Every default here is chosen to be safe on an uncalibrated unit, so the
# ``common`` DB entry can carry them for all units and a per-unit entry only
# needs to record genuine deviations.
# ---------------------------------------------------------------------------


class CalibrationCoordConfig(BaseModel):
    """Default pointing for a calibration run: **the zenith**.

    Resolution order is explicit endpoint parameter -> this -> runtime default,
    and ``None`` on either axis means "decide at runtime from the observatory":

    * ``ra = None`` -> the current LST, i.e. transit.  Deferred to slew time
      because LST advances as the run proceeds (a hardcoded RA would also be
      unobservable for much of the year).
    * ``dec = None`` -> the site's **latitude**, which puts the pointing at the
      zenith.  Constant, so it is resolved as soon as the coordinate is asked
      for.

    Zenith is minimum airmass, hence the least refraction, extinction and
    seeing degradation.  That is not cosmetic here: focus, optical-centre coma
    and stage-shadow geometry are all measured from star *shapes*.  The
    previous default of ``dec = 20.0`` pointed ~10 degrees off zenith at Neot
    Smadar (latitude 30.05) and taxed every calibration run for nothing.
    """

    ra: float | None = None  # J2000 hours; None -> LST at run time (transit)
    dec: float | None = None  # J2000 degrees; None -> site latitude (zenith)


class FocuserCalibrationSettings(BaseModel):
    """Inputs for ``POST /calibrate/focuser`` (the HFD sweep).

    Distinct from the operational ``AutofocusConfig`` (the ps3cli path) because
    the two do not share geometry: ps3cli sweeps the small acquisition sky ROI,
    while this runs FULL FRAME and restricts the metric to the low-coma disk
    taken from ``calibration.optical_center``.
    """

    exposure: float = 5.0  # seconds per sweep frame
    binning: ASI_294MM_SUPPORTED_BINNINGS_LITERAL = 1
    images: int = 7  # V-curve points; MUST be odd so a point sits at the centre
    spacing: int = 50  # focuser ticks between points
    max_tries: int = 3  # re-centred sweeps before giving up
    tolerance_frac: float = 0.025  # fitted-diameter rise defining the tolerance
    fallback_disk_frac: float = 0.6  # of min(nx,ny)/2, when no optical centre exists

    # Travel limits -- every commanded position is clamped to this range, so a
    # bad seed or a runaway donut jump cannot drive the focuser into its stops.
    min_position: int = 0
    max_position: int = 49999  # FocuserConfig constrains positions to < 50000

    # Phase 0 / Phase 2 (regime triage and far-from-focus acquisition)
    #
    # near_hfd_max_px is what MAKES the triage work: without a threshold,
    # "anything extracted" counts as near focus, and a frame full of large
    # defocus donuts is misclassified as near -- the donut branch never runs and
    # the V-curve is fitted to annuli.  Above this measured HFD the frame is
    # treated as far-from-focus and routed to donut acquisition.
    near_hfd_max_px: float = 20.0
    #
    # ...but the HFD alone cannot make that call.  On mast00 2026-10-01, from
    # +-2000 ticks outward the stars were large faint donuts, yet hundreds of
    # small sources (faint lumps, donut fragments) still extracted, each filling
    # the minimum aperture, and the median HFD sat at ~12.7 px from focus to
    # +10000 -- so every run triaged "near".  What did separate them was the
    # number of sources with a bright CORE (>= near_min_peak_snr x noise at the
    # centroid; a donut's centroid is in its hole): 47-99 within 523 ticks of
    # focus, 0-2 from 2000 out.  Fewer than near_min_stars such sources means the
    # frame is not near focus, whatever its HFD says.
    near_min_stars: int = 10
    near_min_peak_snr: float = 10.0  # core (3x3 median at the centroid) / background noise
    #
    # max_best_hfd_px rejects an implausible "solution" by the size of its vertex.
    # It is a weak test: the consistent-star HFD at focus measures 18-27 px on this
    # system (mast02 2026-07-21/22, mast00 2026-10-01), and the false vertices of
    # 2026-10-01 measured 13-20 px -- SMALLER than real focus.  The shape gate
    # below is what tells them apart; this is only a backstop.  (It was 12.0 here,
    # with a comment claiming in-focus stars are "a few px"; that would have
    # rejected every real solution, and only the DB override of 35 kept it out
    # of the way.)
    max_best_hfd_px: float = 35.0
    #
    # The V-curve shape gate.  A vertex is trusted only if the parabola actually
    # describes the samples (R^2 of the D^2 fit) and the curve actually rises away
    # from it (the higher sweep end >= min_edge_rise x Dmin).  Every genuine
    # sweep on record scores R^2 0.93-1.00; the three false solutions of
    # 2026-10-01 -- flat or zig-zag curves through donuts -- scored 0.09-0.61.
    # Only the HIGHER end is tested: a real vertex near one edge rises as little as
    # 1.12x on its short side.
    min_fit_r2: float = 0.85
    min_edge_rise: float = 1.25
    #
    # NOTE: all of these are seeing- and optics-dependent.  The figures above are
    # from three nights on two units; re-check them as more nights accumulate.
    backlash_ticks: int = 200  # approach every sweep from below by this much
    donut_probe_ticks: int = 500  # differential move that calibrates the donut slope
    #                               and resolves inside-vs-outside focus (sign)
    coarse_step_ticks: int = 1000  # cold-start stepping when nothing extracts at all
    coarse_max_steps: int = 8  # give up rather than sweep the whole travel

    # Re-centring a sweep that did not bracket focus.  HFD grows ~linearly with
    # defocus, so the swept arm is extrapolated to its floor rather than stepped
    # by half a span at a time (which crawls, and burns tries, when the seed is
    # far out).  Undershoot deliberately: landing short still brackets on the
    # next sweep, overshooting past focus does not.
    recentre_undershoot_frac: float = 0.15
    max_recentre_ticks: int = 2000  # cap on a single extrapolated jump

    @property
    def note_on_binning(self) -> str:
        """Why binning is a knob here but fixed at 1 for the other two phases.

        HFD is a *relative* focus index, so a binned sweep still finds the same
        vertex and costs a quarter of the detection time.  But the low-coma disk
        is stored in full-detector **bin-1** pixels, so a consumer running at
        bin 2 must halve ``center`` and ``low_coma_radius`` before passing them
        to ``frame_hfd``.  Geometry-producing phases (optical_center, stage) have
        no such freedom -- see their settings.
        """
        return "low-coma disk is bin-1; scale by 1/binning when binning > 1"


class OpticalCenterCalibrationSettings(BaseModel):
    """Inputs for ``POST /calibrate/optical_center``.

    No ``binning``: the measured centre *defines* the bin-1 pixel frame that
    ``OpticalCenterCalibration.image_shape`` and ``.matches()`` guard, and that
    the stage phase later solves against.  Acquiring it at any other binning
    would silently poison every downstream geometric calibration, so the phase
    hardcodes full-frame bin 1 rather than offering a knob that must never move.
    """

    exposure: float = 5.0
    number_of_frames: int = 5  # pooled into ONE weighted fit; per-frame centres
    #                            scatter ~10^2 px, so a single frame is untrustworthy
    coma_tolerance: float = 0.1  # ellipticity budget (photutils e = 1 - b/a) that
    #                              sets low_coma_radius = coma_tolerance / coma_slope
    min_frames_passing: int | None = None  # None -> ceil(number_of_frames / 2)


class StageCalibrationSettings(BaseModel):
    """Inputs for ``POST /calibrate/stage``.

    No ``binning``, for the same reason as the optical centre: the shadow
    centreline is solved against the stored bin-1 optical centre, and the phase
    asserts the frame size matches before it starts.
    """

    exposure: float = 5.0
    n_positions: int = 5  # >= 3 (backlash / nonlinearity / noise averaging)
    span_steps: int | None = None  # half-sweep about the spec estimate; None ->
    #                                max(2000, 5% of travel), computed at run time
    settle_seconds: float = 1.0  # dwell after the stage stops, before exposing
    require_bracketed: bool = True  # refuse to extrapolate s* outside the sweep
    move_to_spec: bool = False  # park at the solved position instead of retracting


class FocusConvergenceCampaignSettings(BaseModel):
    """A commissioning campaign: does focus calibration converge from far out, both ways?

    Everything the focus phase does far from focus rests on evidence from one side
    only.  `analysis/sharpness.py` says so itself -- the metric falls monotonically
    over the frames we have, but *"we have no frames near focus, so the claim 'it
    peaks AT focus' is still untested"* -- and `near_hfd_max_px` / `max_best_hfd_px`
    carry a matching warning that they are ESTIMATES until measured on sky.

    The campaign answers three questions at once by starting runs at known offsets
    either side of a reference focus: does it converge, from how far, and to the
    SAME answer.  The spread of solved positions is the accuracy figure; the
    largest offset that still converges is the capture range; a difference between
    the two signs is the asymmetry the differential donut method is supposed to
    have removed.
    """

    #: Offsets from the reference, in focuser ticks, applied BOTH ways.
    #: Escalating so the cheap informative cases run first -- see `interleave`.
    offsets: list[int] = Field(default_factory=lambda: [500, 2000, 5000, 10000])

    #: Run each (offset, sign) this many times. 1 answers "does it converge";
    #: >=2 starts to answer "to the same place".
    repeats: int = 1

    #: Alternate the sign (+500, -500, +2000, -2000, ...) rather than doing all of
    #: one side first.  A night that ends early then still carries BOTH signs at
    #: every scale reached, which is the comparison the campaign exists to make.
    interleave: bool = True

    #: Abandon a single run after this long and move to the next offset.  A far
    #: start can legitimately take ~10 min (coarse stepping plus up to max_tries
    #: sweeps); beyond this it is stuck and the night is better spent elsewhere.
    run_timeout_seconds: float = 900.0

    #: Stop the whole campaign after this many consecutive failures.  Repeated
    #: failure usually means the sky, the focuser or the mount -- not the offset --
    #: and grinding through the remaining offsets just wastes the window.
    max_consecutive_failures: int = 3

    #: Where results land.  None -> `Filer().accessible_shared_root()`, which PROBES
    #: rather than asking whether a drive letter is mapped.  That distinction is the
    #: point: with the share down but `Z:` still mapped, `Filer.shared.root` remains
    #: `Z:/MAST/<host>/` and writes go nowhere, while the probe correctly yields
    #: `C:/MAST/`.  Set this explicitly to pin the destination regardless.
    products_root: str | None = None

    #: Return the focuser to the reference position when the campaign ends, so the
    #: telescope is left where it was found whatever happened in between.
    restore_focus_on_exit: bool = True


class CalibrationSettings(BaseModel):
    """All calibration inputs, one sub-block per phase plus the shared pointing."""

    coord: CalibrationCoordConfig = Field(default_factory=CalibrationCoordConfig)
    focuser: FocuserCalibrationSettings = Field(default_factory=FocuserCalibrationSettings)
    optical_center: OpticalCenterCalibrationSettings = Field(default_factory=OpticalCenterCalibrationSettings)
    stage: StageCalibrationSettings = Field(default_factory=StageCalibrationSettings)
    campaign: FocusConvergenceCampaignSettings = Field(default_factory=FocusConvergenceCampaignSettings)


# ---------------------------------------------------------------------------
# Products (outputs) -- what the phases WRITE.
# ---------------------------------------------------------------------------


class OpticalCenterCalibration(BaseModel):
    """Optical center and the derived low-coma zone autofocus should keep within.

    ``center_x/center_y`` are the optical axis on the detector; ``low_coma_radius``
    is the radius of the disk about it inside which coma-driven PSF elongation
    stays under ``coma_tolerance`` -- derived from the *measured* coma slope as
    ``low_coma_radius = coma_tolerance / coma_slope`` rather than guessed.  All
    lengths are detector pixels **at bin 1**; ``image_shape`` is the frame they
    were measured on, kept so a consumer can reject a size mismatch.
    """

    center_x: float
    center_y: float
    # px @ bin1: disk about the center autofocus stays within.  NULLABLE on
    # purpose: when the coma slope k is too poorly determined to trust, the
    # radius is stored as None rather than fabricated, and focus falls back to
    # its geometric disk (settings.focuser.fallback_disk_frac).
    low_coma_radius: float | None = None
    coma_slope: float | None = None  # measured elongation per px of field radius (k)
    coma_tolerance: float | None = None  # ellipticity budget used to derive the radius
    image_shape: tuple[int, int]  # (ny, nx) the calibration was measured on

    # provenance / quality
    n_sources: int
    residual_rms: float  # RMS of the axis-to-center fit (px)
    radiality: float  # coma-signal confidence (see imaging.optical_center)
    timestamp: str
    mechanical_epoch: int = 0  # bumps on optics service; groups geometric calibrations

    @property
    def center(self) -> tuple[float, float]:
        return (self.center_x, self.center_y)

    def matches(self, image_shape: tuple[int, int], mechanical_epoch: int) -> bool:
        """Whether this calibration may be used for a frame of ``image_shape``.

        The consumption / fallback rule: use the optical center only when it is
        in the current mechanical epoch and was measured on the same frame size;
        otherwise the caller falls back to the geometric center.
        """
        return self.mechanical_epoch == mechanical_epoch and tuple(self.image_shape) == tuple(image_shape)

    def local_center(self, roi_x: int, roi_y: int) -> tuple[float, float]:
        """The optical center in the coordinates of an ROI sub-frame.

        The stored center is in full-detector pixels; an autofocus frame is
        usually a small ROI whose origin is ``(roi_x, roi_y)``.  Subtracting the
        origin gives the center in the ROI's own frame, which is what
        :func:`imaging.hfd.frame_hfd` expects for its low-coma ``center``.
        """
        return (self.center_x - roi_x, self.center_y - roi_y)


class FocuserCalibration(BaseModel):
    """Best-focus position from autofocus, with provenance + quality.

    The calibration-tracked counterpart to ``FocuserConfig.known_as_good_position``
    (the bare operational value autofocus seeds from and commands): this record
    adds the V-curve quality figures and the temperature behind the thermal
    focus-seed, so a poor calibration can be rejected and drift tracked.

    ``best_position`` is in focuser ticks.  Quality (design sec. 3): the V-curve
    ``tolerance`` (the empirical Critical Focus Zone) and the achieved minimum star
    diameter ``best_hfd``; ``n_samples`` is the count of valid V-curve points behind
    the fit.  ``temperature`` (+ ``temperature_read_time``) is the mirror temperature
    at calibration -- the ``(T, best_position)`` sample the thermal seed model
    accumulates -- ``None`` when no reading was available (never fabricated).
    """

    best_position: int  # focuser ticks
    tolerance: float | None = None  # achieved V-curve tolerance / CFZ (ticks)
    best_hfd: float | None = None  # achieved minimum star diameter at best focus (px)
    n_samples: int = 0  # valid V-curve samples behind the fit
    temperature: float | None = None  # mirror temperature at calibration (deg C)
    temperature_read_time: str | None = None  # when temperature was read (stale-guard)
    timestamp: str


class StageCalibrationConfig(BaseModel):
    """Pick-off stage "spec" position + geometry quality (written by StageCalibrator).

    ``spec_position`` is the 1-DOF stage sweep coordinate that places the
    folding-mirror shadow centerline on the optical center
    (:func:`imaging.stage_geometry.find_spec_stage_position`).  It is the *coarse*
    term of on-fiber placement; the along-centerline residual + the fiber's true
    acceptance point are taken up later by a mount offset + a flux peak-up, so
    ``fiber_offset`` stays ``None`` until a commissioning peak-up (v1 = geometry
    only, carrying an uncharacterized fiber offset).

    A **geometric** calibration: shares ``mechanical_epoch`` with the optical
    center so servicing the optics invalidates the pair together -- a fresh stage
    geometry must never pair with a stale optical center (``slope``, the emergent
    stage->pixel scale, and the fit quality figures back the ``stage-geometry``
    calibration gate).
    """

    spec_position: int  # stage steps placing the centerline on the optical center (s*)
    slope: float | None = None  # B : perp-pixels per stage step (emergent scale)
    fiber_offset: float | None = None  # along-centerline shadow-center->fiber offset (peak-up; v1 None)

    # provenance / quality
    optical_center: tuple[float, float]  # OC the solve was referenced to (px)
    image_shape: tuple[int, int]  # (ny, nx) the frames were measured on
    n_frames: int
    residual_rms: float  # d(s) linear-fit residual (px)
    angle_rms_deg: float  # centerline-orientation spread across frames (deg)
    bracketed: bool  # frames straddled the optical center (interpolated s*)
    timestamp: str
    mechanical_epoch: int = 0

    def matches(self, image_shape: tuple[int, int], mechanical_epoch: int) -> bool:
        """Whether this stage geometry may be used: in-epoch and same frame size."""
        return self.mechanical_epoch == mechanical_epoch and tuple(self.image_shape) == tuple(image_shape)


class CalibrationProducts(BaseModel):
    """What the calibration phases MEASURE and write.

    Every field is ``None`` until a run produces it, and only a successful run
    ever writes one -- these are never hand-authored, because each record
    carries provenance (timestamp, quality figures, temperature) that a
    hand-written value would not have.  In the ``common`` unit entry they are
    therefore all ``None``; real values live in per-unit entries.
    """

    focuser: FocuserCalibration | None = Field(
        default=None,
        description="Best-focus position + V-curve quality + temperature (written by /calibrate/focuser).",
    )
    optical_center: OpticalCenterCalibration | None = Field(
        default=None,
        description="Optical center + low-coma zone (written by /calibrate/optical_center).",
    )
    stage: StageCalibrationConfig | None = Field(
        default=None,
        description="Pick-off stage spec position + geometry (written by /calibrate/stage).",
    )
    # later: thermal_focus_seed, pointing, ...


class CalibrationConfig(BaseModel):
    """The unit's calibration block: settings it reads, products it writes.

    The two halves are deliberately symmetric and deliberately separate.
    ``settings`` is configuration -- edited by people, carried by the ``common``
    entry, inherited by every unit.  ``products`` is measurement -- written only
    by a successful phase, per-unit, never inherited.  Keeping them apart is
    what lets a phase read its own inputs on a unit that has never been
    calibrated (the original design collapsed them onto one key, which could not
    work: the product is absent before the first run).
    """

    settings: CalibrationSettings = Field(
        default_factory=CalibrationSettings,
        description="Inputs for the calibration phases (defaults live in the 'common' unit entry).",
    )
    products: CalibrationProducts = Field(
        default_factory=CalibrationProducts,
        description="Measured calibration products (per-unit; absent until a run writes them).",
    )
