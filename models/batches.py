from pydantic import BaseModel
from ulid import ULID

from common.models.calibration import CalibrationSettings
from common.models.plans import Plan


class Batch(BaseModel):
    ulid: ULID | None = None
    immediate: bool = False  # for immediate execution or merely forecasted
    plans: list[Plan]
    calibration: CalibrationSettings | None = None
    exposure_duration: float | None = None
    number_of_exposures: int | None = None
    predicted_duration: float | None = None

    def model_post_init(self) -> None:
        """
        Make a batch out of the given plans.
        """

        predicted_autofocus_duration = (
            180 if any(plan.autofocus for plan in self.plans) else 0
        )

        self.ulid = self.ulid or ULID()

        self.number_of_exposures = max(
            plan.spec.number_of_exposures
            for plan in self.plans
            if plan.spec.number_of_exposures is not None
        )

        #
        # TBD: How to handle the case where different plans have different exposure durations
        #   Some plans may become over-exposed.
        #
        self.exposure_duration = max(
            plan.spec.exposure_duration
            for plan in self.plans
            if plan.spec.exposure_duration is not None
        )
        self.calibration = CalibrationSettings(lamp_on=False, filter=None)
        max_density = 0
        max_timeout_to_guiding = 0
        for plan in self.plans:
            if (
                plan.timeout_to_guiding
                and plan.timeout_to_guiding > max_timeout_to_guiding
            ):
                max_timeout_to_guiding = plan.timeout_to_guiding

            calibration = plan.spec.calibration
            if calibration is None:
                continue

            if calibration.lamp_on:
                self.calibration.lamp_on = True
                if calibration.filter is not None:
                    density = (
                        int(calibration.filter.replace("ND", ""))
                        if calibration.filter.startswith("ND")
                        else 0
                    )
                    if density > max_density:
                        max_density = density
                        self.calibration.filter = calibration.filter
        self.predicted_duration = (
            predicted_autofocus_duration
            + max_timeout_to_guiding
            + self.exposure_duration * self.number_of_exposures
        )
