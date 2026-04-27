import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from common.activities import Activities, BatchActivities
from common.mast_logging import init_log
from common.models.calibration import CalibrationSettings
from common.models.plans import Plan
from common.models.spectrographs import SpectrographModel

if TYPE_CHECKING:
    from common.interfaces.components import ComponentStatus

logger = logging.Logger("mast." + __name__)
init_log(logger)


class BatchData(BaseModel):
    """Pure data contract for a scheduled batch — no hardware or activity state."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ulid: ULID | None = None
    immediate: bool = False
    plans: list[Plan]
    spec_assignment: SpectrographModel | None = None
    predicted_duration: float | None = None
    exposure_duration: float = 0.0
    number_of_exposures: int = 1


class Batch(BatchData, Activities):
    controller: Any | None = None
    spec_api: Any | None = Field(default=None, exclude=True)
    run_folder: str | None = None

    def model_post_init(self, context: dict[str, Any] | None) -> None:
        Activities.__init__(self)

        self.controller = (
            context["controller"] if context and "controller" in context else None
        )
        assert all(plan.spec_assignment is not None for plan in self.plans), (
            "All plans must have a spectrograph assignment"
        )
        assert all(
            plan.spec_assignment.instrument == self.plans[0].spec_assignment.instrument  # type: ignore
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
        self.exposure_duration = max(
            plan.target.requested_exposure_duration
            for plan in self.plans
            if plan.target.requested_exposure_duration is not None
        )

        self.number_of_exposures = max(
            (
                plan.target.requested_number_of_exposures
                for plan in self.plans
                if plan.target.requested_number_of_exposures is not None
            ),
            default=1,
        )

        requested_calibration = CalibrationSettings(lamp_on=False, filter=None)
        requested_calibration.lamp_on = any(
            plan.spec_assignment.calibration.lamp_on  # type: ignore
            for plan in self.plans
            if plan.spec_assignment.calibration is not None  # type: ignore
        )

        if requested_calibration.lamp_on:
            requested_filters = [
                plan.spec_assignment.calibration.filter  # type: ignore
                for plan in self.plans
                if plan.spec_assignment.calibration is not None  # type: ignore
                and plan.spec_assignment.calibration.lamp_on  # type: ignore
                and plan.spec_assignment.calibration.filter is not None  # type: ignore
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
            instrument=self.plans[0].spec_assignment.instrument,  # type: ignore
            calibration=requested_calibration,
        )

        # TBD: How to handle the case where different plans have different spec settings?

        self.max_timeout_to_guiding = 0
        for plan in self.plans:
            if (
                plan.timeout_to_guiding
                and plan.timeout_to_guiding > self.max_timeout_to_guiding
            ):
                self.max_timeout_to_guiding = plan.timeout_to_guiding

        self.predicted_duration = (
            predicted_autofocus_duration
            + self.max_timeout_to_guiding
            + self.exposure_duration * self.number_of_exposures
        )

    async def get_spec_status(self) -> Any | None:  # ComponentStatus | None
        if not self.spec_api:
            raise Exception("get_spec_status: spec_api is None")
        canonical_response = await self.spec_api.get(method="status")
        if not canonical_response.succeeded:
            canonical_response.log(_logger=logger, label="spec")
            return None
        return canonical_response.value

    def still_have_live_plans(self) -> bool:
        self.live_plans = [p for p in self.live_plans if not p.terminated]
        return bool(self.live_plans)

    async def probe(self):
        self.start_activity(BatchActivities.Probing)
        await asyncio.gather(*[plan.probe() for plan in self.live_plans])
        self.end_activity(BatchActivities.Probing)

    async def dispatch(self):
        self.start_activity(BatchActivities.Dispatching)
        await asyncio.gather(*[plan.dispatch() for plan in self.live_plans])
        self.end_activity(BatchActivities.Dispatching)

    async def wait_for_guiding(self):
        self.start_activity(BatchActivities.WaitingForGuiding)
        await asyncio.gather(*[plan.wait_for_guiding() for plan in self.live_plans])
        self.end_activity(BatchActivities.WaitingForGuiding)

    async def expose(self):
        from common.models.workloads import Workload

        await Workload(work=self).expose()

    async def wait_for_spec_done(self):
        self.start_activity(BatchActivities.WaitingForSpecDone)
        while True:
            await asyncio.sleep(20)
            spec_status: ComponentStatus | None = await self.get_spec_status()
            assert spec_status is not None, "spec_status should not be None"

            if not spec_status.operational:
                for err in spec_status.why_not_operational:
                    logger.error(f"spec not operational: {err}")
                self.end_activity(BatchActivities.WaitingForSpecDone)
                await self.terminate(
                    reason="failed", details=["spectrograph became non-operational"]
                )
                return

            logger.info("wait_for_spec_done: " + json.dumps(spec_status, indent=2))
            if spec_status.activities == Activities.Idle:
                logger.info("spec is Idle")
                self.end_activity(BatchActivities.WaitingForSpecDone)
                break
            else:
                logger.info(
                    f"spec is busy: activities: {spec_status.activities} "
                    + f"({spec_status.activities_verbal})"
                    + ", waiting..."
                )

    async def terminate(
        self, reason: Literal["failed", "rejected", "completed"], details: list[str]
    ):
        self.start_activity(BatchActivities.Aborting)
        self.terminated = True
        await asyncio.gather(
            *[
                plan.terminate(reason=reason, details=details)
                for plan in self.live_plans
            ]
        )
        self.end_activity(BatchActivities.Aborting)
        self.end_activity(BatchActivities.Executing)

    async def execute(self, controller):
        from common.api import SpecApi
        from common.config import Config

        local_site = Config().local_site
        assert local_site is not None

        self.spec_api = SpecApi(local_site.name)
        self.terminated = False
        self.start_activity(BatchActivities.Executing)
        self.live_plans: list[Plan] = self.plans.copy()

        for plan in self.live_plans:
            plan.prepare(controller)

        await self.probe()
        if not self.still_have_live_plans():
            await self.terminate(
                reason="rejected", details=["no live plans after probing"]
            )
            return

        await self.dispatch()
        if not self.still_have_live_plans():
            await self.terminate(
                reason="failed", details=["no live plans after dispatching"]
            )
            return

        await self.wait_for_guiding()
        if not self.still_have_live_plans():
            await self.terminate(
                reason="failed", details=["no live plans after waiting for guiding"]
            )
            return

        await self.expose()
        if self.terminated:
            return

        await self.wait_for_spec_done()
        if self.terminated:
            return

        await asyncio.gather(
            *[
                plan.terminate(
                    reason="completed", details=["executed as part of batch"]
                )
                for plan in self.live_plans
            ]
        )

        self.end_activity(BatchActivities.Executing)
