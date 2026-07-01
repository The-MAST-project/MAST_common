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
    low_coma_radius: float  # px @ bin1: disk about the center autofocus stays within
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
        return (
            self.mechanical_epoch == mechanical_epoch
            and tuple(self.image_shape) == tuple(image_shape)
        )

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


class CalibrationConfig(BaseModel):
    """The unit's persisted calibration state (extended per calibration concern)."""

    optical_center: OpticalCenterCalibration | None = Field(
        default=None,
        description="Optical center + low-coma zone (written by the optical-center finder).",
    )
    focuser: FocuserCalibration | None = Field(
        default=None,
        description="Best-focus position + V-curve quality + temperature (written by autofocus).",
    )
    # later: stage_geometry (pick-off fiber), thermal_focus_seed, pointing, ...
