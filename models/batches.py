from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from ulid import ULID

from common.activities import Activities
from common.mast_logging import get_logger
from common.models.calibration import CalibrationSettings
from common.models.plans import Plan
from common.models.spectrographs import SpectrographModel

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class Batch(BaseModel, Activities):
    ulid: ULID | None = None
    immediate: bool = False  # for immediate execution or merely forecasted
    plans: list[Plan]
    spec: SpectrographModel | None = None
    predicted_duration: float | None = None
    controller: Any | None = None
    spec_api: Any | None = Field(default=None, exclude=True)
    run_folder: str | None = None

    def model_post_init(self, context: dict[str, Any] | None) -> None:
        Activities.__init__(self)

        self.controller = context["controller"] if context and "controller" in context else None
        assert all(plan.spec is not None for plan in self.plans), "All plans must have a spectrograph assignment"
        assert all(
            plan.spec.instrument == self.plans[0].spec.instrument  # type: ignore
            for plan in self.plans
        ), "All plans must have the same spectrograph instrument"

        # if any of the plans require autofocus, we need to add a predicted autofocus duration to the batch
        predicted_autofocus_duration = 180 if any(plan.autofocus for plan in self.plans) else 0

        self.ulid = self.ulid or ULID()

        #
        # We need to merge the spectrograph assignments of the plans into a single assignment for the batch.
        #

        #
        # TBD: How to handle the case where different plans have different exposure durations
        #   Some plans may become over-exposed.
        #
        # The exposure series is the spectrograph's (plans/control_acquisition_design.md 3.2).
        self.exposure_duration = max(
            plan.spec.requested_exposure_duration  # type: ignore[union-attr]
            for plan in self.plans
            if plan.spec.requested_exposure_duration is not None  # type: ignore[union-attr]
        )

        self.number_of_exposures = max(
            (
                plan.spec.requested_number_of_exposures  # type: ignore[union-attr]
                for plan in self.plans
                if plan.spec.requested_number_of_exposures is not None  # type: ignore[union-attr]
            ),
            default=1,
        )

        requested_calibration = CalibrationSettings(lamp_on=False, filter=None)
        requested_calibration.lamp_on = any(
            plan.spec.calibration.lamp_on  # type: ignore
            for plan in self.plans
            if plan.spec.calibration is not None  # type: ignore
        )

        if requested_calibration.lamp_on:
            requested_filters = [
                plan.spec.calibration.filter  # type: ignore
                for plan in self.plans
                if plan.spec.calibration is not None  # type: ignore
                and plan.spec.calibration.lamp_on  # type: ignore
                and plan.spec.calibration.filter is not None  # type: ignore
            ]
            filter_densities = [int(f.replace("ND", "")) for f in requested_filters if f.startswith("ND")]
            requested_calibration.filter = str(max(filter_densities)) if filter_densities else None
            if requested_calibration.filter is not None:
                requested_calibration.filter = f"ND{requested_calibration.filter}"

        self.spec = SpectrographModel(
            instrument=self.plans[0].spec.instrument,  # type: ignore
            calibration=requested_calibration,
        )

        # TBD: How to handle the case where different plans have different spec settings?

        self.max_timeout_to_guiding = 0
        for plan in self.plans:
            if plan.timeout_to_guiding and plan.timeout_to_guiding > self.max_timeout_to_guiding:
                self.max_timeout_to_guiding = plan.timeout_to_guiding

        self.predicted_duration = (
            predicted_autofocus_duration + self.max_timeout_to_guiding + self.exposure_duration * self.number_of_exposures
        )

    # Executing a batch is not implemented: the stepped probe/dispatch/wait methods it drove
    # left `Plan` for MAST_control's per-unit `PlanExecution`, and a batch -- several plans
    # sharing units and one spectrograph exposure -- needs its own design on top of that
    # (plans/control_acquisition_design.md 10). The scheduler produces no batches today.
