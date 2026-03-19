from pydantic import BaseModel
from ulid import ULID

from common.models.calibration import CalibrationSettings
from common.models.plans import Plan
from common.models.spectrographs import SpectrographModel


class Batch(BaseModel):
    ulid: ULID | None = None
    immediate: bool = False  # for immediate execution or merely forecasted
    plans: list[Plan]
    spec_assignment: SpectrographModel | None = None
    predicted_duration: float | None = None

    def model_post_init(self) -> None:
        """
        Make a batch out of the given plans.
        """

        assert all(plan.spec_assignment is not None for plan in self.plans), (
            "All plans must have a spectrograph assignment"
        )
        assert all(
            plan.spec_assignment.instrument == self.plans[0].spec_assignment.instrument
            for plan in self.plans
        ), "All plans must have the same spectrograph instrument"

        # if any of the plans require autofocus, we need to add a predicted autofocus duration to the batch
        predicted_autofocus_duration = (
            180 if any(plan.autofocus for plan in self.plans) else 0
        )

        self.ulid = self.ulid or ULID()

        #
        # We need to merge the spectrograph assignments of the plans into a single assignment for the batch.
        #

        #
        # TBD: How to handle the case where different plans have different exposure durations
        #   Some plans may become over-exposed.
        #
        exposure_duration = max(
            [plan.spec_assignment.exposure_duration for plan in self.plans]
        )

        number_of_exposures = (
            max(
                [
                    plan.spec_assignment.number_of_exposures
                    for plan in self.plans
                    if plan.spec_assignment.number_of_exposures is not None
                ]
            )
            or 1
        )

        requested_calibration = CalibrationSettings(lamp_on=False, filter=None)
        requested_calibration.lamp_on = any(
            plan.spec_assignment.calibration.lamp_on
            for plan in self.plans
            if plan.spec_assignment.calibration is not None
        )

        if requested_calibration.lamp_on:
            requested_filters = [
                plan.spec_assignment.calibration.filter
                for plan in self.plans
                if plan.spec_assignment.calibration is not None
                and plan.spec_assignment.calibration.lamp_on
                and plan.spec_assignment.calibration.filter is not None
            ]
            filter_densities = [
                int(f.replace("ND", ""))
                for f in requested_filters
                if f.startswith("ND")
            ]
            requested_calibration.filter = (
                str(max(filter_densities)) if filter_densities else None
            )
            if requested_calibration.filter is not None:
                requested_calibration.filter = f"ND{requested_calibration.filter}"

        self.spec_assignment = SpectrographModel(
            instrument=self.plans[0].spec_assignment.instrument,
            exposure_duration=exposure_duration,
            number_of_exposures=number_of_exposures,
            calibration=requested_calibration,
        )

        # TBD: How to handle the case where different plans have different spec settings?

        max_timeout_to_guiding = 0
        for plan in self.plans:
            if (
                plan.timeout_to_guiding
                and plan.timeout_to_guiding > max_timeout_to_guiding
            ):
                max_timeout_to_guiding = plan.timeout_to_guiding

        self.predicted_duration = (
            predicted_autofocus_duration
            + max_timeout_to_guiding
            + exposure_duration * number_of_exposures
        )
