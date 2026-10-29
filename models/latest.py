"""The latest runs, one shape for all of them: `FullUnitStatus.latest.<kind>` on a unit,
`SpecStatus.latest.<kind>` on the spectrograph.

Each kind of run -- a focus-on-star, an acquisition, an exposure series -- keeps a record of
its latest run, current or finished. They all extend `LatestBase`: when it started and ended,
whether it succeeded and why not, where its products are written and where they end up on the
share, and the plan it ran under. Each adds what is particular to its kind.

A run's record is replaced, by a fresh one, before the endpoint that started it answers; so
the first status read after the request describes that run, never the one before. When the
run ends, the record is also written into its products folder (`write`), so the share holds
the same summary the status reported.
"""

from __future__ import annotations

import datetime
import json
import os
import threading
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, computed_field

from common.mast_logging import get_logger

logger = get_logger(__name__)

#: The record's file in its products folder.
LATEST_FILE = "latest.json"


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def products_paths(folder: str | os.PathLike | None) -> tuple[str | None, str | None]:
    """``folder``, forward-slashed, and where it ends up on this machine's share -- known when
    the run starts, before anything has moved. The share's is None for a folder under no
    filing area, and while the share is demoted: its products then go to the local fallback."""
    if folder is None:
        return None, None
    from common.filer import Filer, FilingArea

    local = os.fspath(folder).replace("\\", "/")
    filer = Filer()
    if filer.is_demoted(FilingArea.SHARE):
        return local, None
    try:
        shared = filer.to_top(FilingArea.SHARE, local)
    except ValueError:
        shared = None
    return local, shared


class LatestBase(BaseModel):
    """What every latest-run record says."""

    started: str | None = None  # ISO UTC; None: no run yet
    ended: str | None = None  # None while running
    succeeded: bool | None = None  # None while running
    errors: list[str] = Field(default_factory=list)
    #: Where the run writes its products: the RAM disk, or its fallback.
    local_products_path: str | None = None
    #: Where those products end up: this machine's folder on the share. None while the
    #: share is demoted, when they go to its local fallback instead.
    shared_products_path: str | None = None
    #: The plan the run was for, if any (plans/control_acquisition_design.md 6.6).
    plan_id: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def running(self) -> bool:
        return self.started is not None and self.ended is None

    def begin(self, folder: str | os.PathLike | None = None, plan_id: str | None = None):
        """Mark the run started, with its products folder. Returns the record."""
        self.started = _now()
        self.products_at(folder)
        self.plan_id = plan_id
        return self

    def products_at(self, folder: str | os.PathLike | None) -> None:
        """Where the run's products go, for a run that learns it only after it started."""
        self.local_products_path, self.shared_products_path = products_paths(folder)

    def finish(self, succeeded: bool, errors: list[str] | None = None) -> None:
        """Mark the run ended. Idempotent: a second call changes nothing."""
        if self.ended is not None:
            return
        self.ended = _now()
        self.succeeded = succeeded
        self.errors.extend(e for e in (errors or []) if e not in self.errors)

    def write(self, name: str = LATEST_FILE) -> str | None:
        """Write the record into its products folder, as a product, and hand it to the mover.
        Its path, or None without a folder. Never raises: the record must not fail the run."""
        if self.local_products_path is None:
            return None
        from common.filer import Filer, MoveGuardian

        path = os.path.join(self.local_products_path, name)
        try:
            os.makedirs(self.local_products_path, exist_ok=True)
            with MoveGuardian().protect(path), open(path, "w", encoding="utf-8") as f:
                json.dump(self.model_dump(mode="json"), f, indent=2)
            Filer().move_ram_to_shared(path)
        except Exception as e:  # noqa: BLE001 -- the record must never fail the run it describes
            logger.warning(f"could not write '{path}': {e}")
            return None
        return path


class LatestFocusOnStar(LatestBase):
    """`PUT /unit/focus_on_star`: one isolated catalog star on the fibre pixel, then a
    narrow-field HFD V-curve around it. Also what `GET /unit/focus_on_star_status` returns."""

    phase: str | None = None  # selecting | slewing | acquiring | probing | sweeping | finishing
    outcome: str | None = None  # solved | failed | aborted
    params: dict[str, Any] | None = None
    sky_position: tuple[float, float] | None = Field(default=None, description="(ra hours, dec degrees), J2000")
    star: dict[str, Any] | None = None
    focus_center: tuple[float, float] | None = Field(default=None, description="(x, y), bin-1 pixels")
    box: tuple[int, int, int, int] | None = Field(default=None, description="(x0, x1, y0, y1), sweep pixels")
    exposure: float | None = None
    start_position: int | None = None
    best_position: int | None = None
    final_position: int | None = None
    #: What says the unit is near focus: "calibration" (a /calibrate/focuser product) or
    #: "known_as_good_position" (the last ps3cli autofocus, used when there is no product).
    focus_reference: str | None = None
    #: The V-curve analysis (MAST_unit's `HFDAutofocusStatus`).
    analysis: Any = None


class AcquisitionState(StrEnum):
    """Where the latest `start_acquisition_and_guiding` run is."""

    RUNNING = "running"  # accepted: phases, or the guider calibrating, in progress
    GUIDING = "guiding"  # guiding, locked on a star (PHD2 state exactly "Guiding")
    ENDED = "ended"  # guiding stopped, after it had locked
    FAILED = "failed"
    ABORTED = "aborted"  # cancelled: `Acquiring` cleared, or `unit/abort`


class LatestAcquisitionPhase(BaseModel):
    """One phase of an acquisition (`sky`, `spec`), from its corrections."""

    steps: int = 0  # corrections applied
    tolerance_ra: float | None = None  # arcsec
    tolerance_dec: float | None = None
    last_ra_delta: float | None = None  # arcsec, the last solve's offset from target
    last_dec_delta: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def within_tolerance(self) -> bool | None:
        if None in (self.tolerance_ra, self.tolerance_dec, self.last_ra_delta, self.last_dec_delta):
            return None
        return abs(self.last_ra_delta) <= self.tolerance_ra and abs(self.last_dec_delta) <= self.tolerance_dec  # type: ignore[operator]


class LatestAcquisition(LatestBase):
    """`PUT /unit/start_acquisition_and_guiding`. `succeeded` once the guider has locked on a
    star -- the acquisition's goal -- and the run goes on, guiding, until it ends."""

    state: AcquisitionState = AcquisitionState.RUNNING
    target_ra_hours: float | None = None
    target_dec_degrees: float | None = None
    phases: dict[str, LatestAcquisitionPhase] = Field(default_factory=dict)
    handed_over: bool = False  # to the guider, automatically
    solver: str | None = None


class LatestExposure(LatestBase):
    """`PUT /unit/expose`: an exposure series."""

    exposure_seconds: float | None = None
    repeats: int | None = None  # requested
    completed: int = 0  # frames saved
    last_frame: str | None = None


class LatestSpiral(LatestBase):
    """The operator spiral session, `PUT /unit/spiral_new_path` to `spiral_end_path`.

    An imaging session (`frames`) has succeeded once the shift is measured; one closed
    without it -- left an hour with no step, superseded by a new session, aborted, or its
    final frame failed -- has not. A frameless session takes no frames and measures
    nothing: it has succeeded when `spiral_end_path` closed it, and not when it was
    abandoned, superseded or aborted. Whether the caller's own walk completed is in the
    caller's record, not here."""

    #: False for a session that only moves the mount, leaving the frames to `/expose`.
    frames: bool | None = None
    x_step_arcsec: float | None = None
    y_step_arcsec: float | None = None
    exposure_seconds: float | None = None  # None for a frameless session
    save_intermediate_exposures: bool | None = None
    steps: int = 0  # taken, the reference not counted
    last_frame: str | None = None  # on the unit; its name under `shared_products_path` on the share
    shift: dict[str, Any] | None = None  # the measurement `spiral_end_path` returned

    def stepped(self, frame: str | os.PathLike | None = None) -> None:
        """One more step, and the frame it saved, if any."""
        self.steps += 1
        if frame is not None:
            self.last_frame = os.fspath(frame).replace("\\", "/")


class LatestSpecExposure(LatestBase):
    """The spectrograph's exposure series: a plan's or a batch's (`execute_assignment`), or an
    operator's (`acquire`). `SpecStatus.latest.exposure`.

    A failed exposure does not end the series: it is recorded (`fail`) and the next one is
    tried. The series succeeds if it saved any frame (`finish_series`); `errors` says which
    did not. Only an abort (`cancel`) stops it early, and an aborted series has not succeeded.

    Deepspec exposes its bands on a thread each, so frames and errors are recorded through
    `frame_saved` and `fail`, which serialise them."""

    instrument: str | None = None  # highspec | deepspec
    exposure_duration: float | None = None  # seconds, requested
    number_of_exposures: int | None = None  # requested, per band for deepspec
    lamp_on: bool | None = None  # the ThAr lamp
    filter: str | None = None  # the ThAr filter
    batch_id: str | None = None
    completed: int = 0  # frames saved, every band together
    last_frame: str | None = None

    _lock: threading.Lock = PrivateAttr(default_factory=threading.Lock)
    _cancelled: threading.Event = PrivateAttr(default_factory=threading.Event)

    def frame_saved(self, path: str | os.PathLike | None = None) -> None:
        """One more frame saved, at ``path`` when its name is known."""
        with self._lock:
            self.completed += 1
            if path is not None:
                self.last_frame = os.fspath(path).replace("\\", "/")

    def fail(self, error: str) -> None:
        """Record an error now; `succeeded` is decided when the run finishes."""
        with self._lock:
            if error not in self.errors:
                self.errors.append(error)

    def cancel(self, reason: str = "aborted") -> None:
        """Stop the series: each exposure loop checks `cancelled` before its next exposure."""
        self.fail(reason)
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def finish_series(self) -> None:
        """End the series: succeeded if it saved any frame and was not aborted."""
        self.finish(self.completed > 0 and not self.cancelled)


class SkyQualityWindow(BaseModel):
    """A guiding session's guide frames over the last `seconds`. Medians throughout: one bad
    frame -- a cosmic ray, a gust -- moves them by one sample, not by its size."""

    seconds: int
    samples: int = 0  # guide frames measured
    #: First to last sample: short of `seconds` early in a session, or after a gap.
    span_seconds: float = 0.0
    hfd_px: float | None = None  # the guide star's half-flux diameter, guide pixels
    hfd_arcsec: float | None = None  # None without a plate scale
    hfd_arcsec_spread: float | None = None  # MAD scaled to a standard deviation
    snr: float | None = None
    star_mass: float | None = None
    #: `star_mass` over the session's baseline: below 1, less light than at the start.
    transparency: float | None = None
    star_lost: int = 0  # PHD2 `StarLost` events
    rejected: int = 0  # guide frames PHD2 flagged (`ErrorCode`) or that had no usable HFD


class LatestGuidingSkyQuality(LatestBase):
    """The sky, as PHD2's guide frames see it, over one guiding session -- `StartGuiding` to
    `GuidingStopped`. Seeing from the guide star's HFD; transparency from its StarMass,
    relative to the session's first minute, so it says how the sky changed, not how good it
    was to begin with. Frames taken while settling are left out: the star is moving.

    PHD2's HFD is its own measure, through the guide exposure and binning: read it as
    delivered image size, comparable within a session and between sessions of one unit."""

    #: Arcseconds per guide pixel, binning included. None: the plate scale was unknown.
    plate_scale_arcsec: float | None = None
    binning: int | None = None
    #: Median StarMass over the session's first `baseline_seconds`; None until then.
    baseline_star_mass: float | None = None
    baseline_seconds: int = 60
    samples: int = 0  # over the whole session
    star_lost: int = 0
    rejected: int = 0
    last_sample: str | None = None  # ISO UTC
    windows: list[SkyQualityWindow] = Field(default_factory=list)  # shortest first


class LatestSkyQuality(BaseModel):
    """`FullUnitStatus.latest.sky_quality`."""

    while_guiding: LatestGuidingSkyQuality | None = None


class LatestRecords(BaseModel):
    """`FullUnitStatus.latest`: each kind's latest run, None until one has happened."""

    focus_on_star: LatestFocusOnStar | None = None
    acquisition: LatestAcquisition | None = None
    exposure: LatestExposure | None = None
    spiral: LatestSpiral | None = None
    sky_quality: LatestSkyQuality | None = None


class LatestSpecRecords(BaseModel):
    """`SpecStatus.latest`: the spectrograph's latest runs, None until one has happened."""

    exposure: LatestSpecExposure | None = None
