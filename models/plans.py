import asyncio
import datetime
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import tomlkit
import tomlkit.exceptions
import ulid
from pydantic import BaseModel, Field, ValidationError
from pydantic.config import ConfigDict

from common.activities import Activities, AssignmentActivities, UnitActivities
from common.api import ApiDomain, SpecApi, UnitApi
from common.canonical import CanonicalResponse
from common.config import Config
from common.interfaces.components import ComponentStatus
from common.mast_logging import init_log
from common.models.constraints import ConstraintsModel
from common.models.events import EventModel
from common.models.spectrographs import SpectrographModel
from common.models.targets import Target
from common.utils import OperatingMode, function_name

if TYPE_CHECKING:
    from common.tasks.models import GatherResponse

logger = logging.Logger("mast." + __name__)
init_log(logger)


class Plan(BaseModel, Activities):
    target: Target
    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,  # Allow non-Pydantic types like UnitApi
    )

    ulid: str | None = Field(default=None, description="Unique ID")
    file: str | None = None
    owner: str | None = None
    merit: int | None = 1
    quorum: int | None = Field(default=1, description="Least number of units")
    timeout_to_guiding: int | None = Field(
        default=600, description="How long to wait for all units to achieve 'guiding'"
    )
    autofocus: bool | None = Field(
        default=False, description="Should the units start with 'autofocus'"
    )
    spec: SpectrographModel
    run_folder: str | None = None
    production: bool | None = Field(
        default=True, description="if 'false' some availability tests are more relaxed"
    )
    events: list[EventModel] | None = None  # things that happened to this plan
    constraints: ConstraintsModel | None = None
    commited_unit_apis: list[UnitApi] = []  # the units that committed to this task

    # File and runtime fields
    toml_file: str | None = Field(
        default=None, description="Path to the TOML file containing the plan definition"
    )
    activities: int = Field(default=0, description="Current activities bitmask")
    timings: dict = Field(
        default_factory=dict, description="Timing information for task execution"
    )
    # unit_assignments: list["AssignmentEnvelope"] = Field(
    #     default_factory=list,
    #     description="List of unit assignments",
    #     alias="unit_assignments",
    # )
    # spec_assignment: Any | None = Field(  # AssignmentEnvelope | None
    #     default=None,
    #     description="Spectrograph assignment if any",
    #     alias="spec_assignment",
    # )
    spec_api: SpecApi | None = Field(
        default=None, description="API client for spectrograph communication"
    )

    # @computed_field
    # @property
    # def remote_unit_assignments(self) -> list["AssignmentEnvelope"]:
    #     from common.models.assignments import (
    #         Initiator,
    #         UnitAssignmentModel,
    #     )
    #     from common.models.transmited_assignments import AssignmentEnvelope

    #     ret: list[AssignmentEnvelope] = []
    #     initiator = Initiator.local_machine()
    #     for key in list(self.unit.keys()):
    #         unit_assignment: UnitAssignmentModel = UnitAssignmentModel(
    #             initiator=initiator,
    #             target=Target(ra=self.unit[key].ra, dec=self.unit[key].dec),
    #             plan=self,
    #         )

    #         units_specifier = parse_units(key)
    #         if units_specifier:
    #             units = AssignmentEnvelope.from_units_specifier(
    #                 units_specifier, unit_assignment
    #             )
    #             if units:
    #                 ret += units
    #     return ret

    # @computed_field
    # @property
    # def remote_spec_assignment(self) -> Any | None:  # AssignmentEnvelope | None
    #     from common.models.assignments import Initiator, SpectrographAssignmentModel

    #     local_site = Config().local_site
    #     assert local_site is not None
    #     spec_hostname = local_site.spec_host
    #     if spec_hostname is None:
    #         return
    #     fqdn = f"{spec_hostname}.{local_site.domain}"
    #     try:
    #         ipaddr = socket.gethostbyname(spec_hostname)
    #     except socket.gaierror:
    #         ipaddr = None

    #     spec_model = make_spec_model(self.model_extra.get("spec"))  # type: ignore
    #     if not spec_model:
    #         logger.error("cannot create a spectrograph model, aborting!")
    #         return None
    #     if not spec_model.instrument:
    #         logger.error("spectrograph model has no instrument, aborting!")
    #         return None

    #     initiator = Initiator.local_machine()
    #     try:
    #         spec_assignment = SpectrographAssignmentModel(
    #             instrument=spec_model.instrument,
    #             initiator=initiator,
    #             # plan=self,
    #             spec=spec_model,
    #         )
    #     except ValidationError as e:
    #         for err in e.errors():
    #             logger.error(f"ERR:\n  {err}")
    #         raise

    #     return AssignmentEnvelope(
    #         hostname=spec_hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=spec_assignment
    #     )

    @classmethod
    def from_toml_file(cls, toml_file: str):
        """
        Loads and canonicalizes a TOML model from a plan file and ensures it has required fields.

        If the filename complies with the expected naming convention for plan files (PLAN_*.toml),
         the ULID is extracted from the filename and set as the plan's ulid (if not already set or not he same).
        If the filename does not comply with the expected naming convention:
        - if it contains a ulid field, it is used and the file is copied to the same directory with the correct name.
        - if it does not contain a ulid field, a new ULID is generated and the file is copied to the same directory with the correct name.

        Args:
            toml_file: Path to a TOML format plan definition file.


        Returns:
            Plan: The loaded and validated plan model.

        Raises:
            FileNotFoundError: If the TOML file does not exist
            tomlkit.exceptions.TOMLKitError: If the TOML file is invalid
            OSError: If there are file read/write errors
        """
        toml_path = Path(toml_file)
        if not toml_path.exists():
            raise FileNotFoundError(f"Plan file not found: {toml_file}") from None

        real_path = toml_path.resolve()
        folder = real_path.parent
        basename = toml_path.name
        if (
            len(basename) == 36
            and basename.startswith("PLAN_")
            and basename.endswith(".toml")
        ):
            # Filename complies with PLAN_*.toml, extract ULID
            ulid_str = basename[5:-5]
            ulid_from_basename = ulid.ULID.from_str(ulid_str)
        else:
            # Filename does not comply, generate and enforce new ULID
            ulid_from_basename = ulid.ULID()
            basename = f"PLAN_{str(ulid_from_basename)}.toml"
            new_path = folder / basename
            if not new_path.exists():
                shutil.copy(toml_file, str(new_path))
                logger.warning(
                    f"Copied plan file '{toml_file}' to comply with naming convention: '{new_path}'"
                )
            real_path = new_path.resolve()

        try:
            with open(real_path) as fp:
                toml_doc = tomlkit.load(fp)
        except Exception as e:
            import traceback

            traceback.print_exc()
            raise Exception(f"Invalid TOML file {toml_file}: {str(e)}") from e

        if "ulid" not in toml_doc or toml_doc["ulid"] != str(ulid_from_basename):
            toml_doc["ulid"] = str(ulid_from_basename)
            # Rewrite the TOML file with the correct ULID
            try:
                with open(real_path, "w") as fp:
                    fp.write(tomlkit.dumps(toml_doc))
                logger.warning(
                    f"Updated ULID in plan file '{real_path}' to '{ulid_from_basename}'"
                )
            except Exception as e:
                import traceback

                traceback.print_exc()
                raise Exception(
                    f"Failed to update ULID in TOML file {real_path}: {str(e)}"
                ) from e
        try:
            new_plan = Plan(**toml_doc)  # type: ignore
        except ValidationError as e:
            for err in e.errors():
                logger.error(f"ValidationError:\n  {err}")
            raise ValidationError from e

        return new_plan

    def __repr__(self):
        return f"<Plan>(ulid='{self.ulid}')"

    @staticmethod
    async def api_coroutine(
        api, method: str, sub_url: str, data=None, _json: dict | None = None
    ):
        """
        An asynchronous coroutine for remote APIs

        :param api:
        :param method:
        :param sub_url:
        :param data:
        :param _json:
        :return:
        """

        response = None
        try:
            if method == "GET":
                response = await api.get(sub_url)
            elif method == "PUT":
                response = await api.put(sub_url, data=data, json=_json)
        except Exception as e:
            logger.error(f"api_coroutine: error {e}")
            raise
        return response

    async def fetch_statuses(
        self, units: list[UnitApi], spec: SpecApi | None = None
    ) -> tuple[list["GatherResponse"], Any | None]:
        """Asynchronously fetch status information from multiple units and optionally a spectrograph.

        This method makes parallel API calls to get the status of all specified units and
        optionally a spectrograph. It uses asyncio.gather to run all requests concurrently.

        Args:
            units: List of UnitApi instances representing the telescope units to query
            spec: Optional SpecApi instance for querying a spectrograph's status

        Returns:
            If spec is None:
                list[GatherResponse]: List of status responses from units, in same order as input
            If spec is provided:
                tuple[list[GatherResponse], GatherResponse | None]: Tuple containing:
                    - List of unit status responses
                    - Spectrograph status response or None if failed

        The GatherResponse type can be either:
            - CanonicalResponse: Successful API response with status data
            - BaseException: If the API call failed
        """
        tasks = [
            self.api_coroutine(api=unit_api, method="GET", sub_url="status")
            for unit_api in units
        ]

        if spec:
            tasks.append(self.api_coroutine(api=spec, method="GET", sub_url="status"))
        all_status_responses: list[GatherResponse] = await asyncio.gather(
            *tasks, return_exceptions=True
        )

        if spec:
            return all_status_responses[:-1], all_status_responses[-1]
        return all_status_responses, None

    async def get_spec_status(self) -> ComponentStatus | None:
        if not self.spec_api:
            raise Exception(f"{function_name()}: spec_api is None")

        canonical_response = await self.spec_api.get(method="status")
        if not canonical_response.succeeded:
            canonical_response.log(_logger=logger, label="spec")
            await self.abort()
            self.end_activity(AssignmentActivities.ExposingSpec)
            return None

        return canonical_response.value

    def terminate(
        self, reason: Literal["failed", "rejected", "completed"], details: list[str]
    ):
        """
        Handles the task's termination
        :param reason:
        :param details:
        :return:
        """
        self.controller.task_in_progress = None

        logger.error(f"terminating task '{self.ulid}', {reason=}, {details=}")

        self.add_event(EventModel(what=reason, details=details))

        if not self.model_extra or "toml_file" not in self.model_extra:
            logger.error(f"cannot get 'toml_file' from {self.model_extra=}")
        else:
            current_path = Path(self.model_extra["toml_file"])
            sub_folder = "failed" if reason in ["failed", "rejected"] else "completed"
            new_path = current_path.parent.parent / sub_folder / current_path.name
            os.makedirs(new_path.parent, exist_ok=True)
            shutil.move(str(current_path), str(new_path))
            logger.info(
                f"moved plan '{self.ulid}' from {str(current_path)} to {str(new_path)}"
            )

    def add_event(self, event: EventModel):
        """
        Adds an event to the task's history
        :param event:
        :return:
        """
        if self.model_extra and "toml_file" in self.model_extra:
            file = self.model_extra["toml_file"]

            with open(file) as f:
                toml_doc = tomlkit.load(f)

            if "events" not in toml_doc:
                toml_doc["events"] = tomlkit.aot()

            new_event = tomlkit.table()
            new_event.add("what", event.what)
            new_event.add("details", event.details)
            new_event.add("when", event.when)
            toml_doc["events"].append(new_event)  # type: ignore

            with open(file, "w") as f:
                f.write(tomlkit.dumps(toml_doc))

    async def execute(self, controller):
        """
        Checks if the allocated components (units and spectrograph) are available and operational
        Dispatches assignments to units and waits for a quorum of them to reach 'guiding'
        Sends the assignment to the spectrograph
        Waits for spectrograph to finish the assignment
        Tells units to end 'guiding'
        :return:
        """
        local_site = Config().local_site
        assert local_site is not None

        self.spec_api = SpecApi(local_site.name)
        self.controller = controller
        unit_apis: list[UnitApi] = []

        for assignment in self.remote_unit_assignments:
            unit_apis.append(UnitApi(ipaddr=assignment.ipaddr, domain=ApiDomain.Unit))

        self.start_activity(AssignmentActivities.Executing)

        # Phase #1: check the required components are operational
        self.start_activity(AssignmentActivities.Probing)
        if not unit_apis or not self.spec_api:
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            self.terminate(
                reason="rejected",
                details=["no units assigned to this task"],
            )
            return
        canonical_unit_responses, spec_response = await self.fetch_statuses(
            unit_apis, self.spec_api
        )

        # see what units respond at all
        detected_unit_apis = [unit_api for unit_api in unit_apis if unit_api.detected]
        n_detected = len(detected_unit_apis)
        if self.quorum is not None and n_detected < self.quorum:
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            if n_detected == 0:
                self.terminate(
                    reason="rejected",
                    details=[
                        f"no units quorum, no units were detected (required: {self.quorum})"
                    ],
                )
            else:
                self.terminate(
                    reason="rejected",
                    details=[
                        f"no units quorum, detected only {n_detected} "
                        + f"({[unit_api.hostname for unit_api in detected_unit_apis]}), "
                        + f"required {self.quorum}"
                    ],
                )
            return
        logger.info(
            f"'detected_units' quorum achieved ({n_detected} detected out of {self.quorum} required)"
        )

        if not self.spec_api.detected:
            # spec does not respond
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            self.terminate(reason="rejected", details=["spec not detected"])
            return

        # enough units were detected (they answered to API calls), now check if they are operational
        operational_unit_apis = []
        for i, response in enumerate(canonical_unit_responses):
            unit_api = unit_apis[i]

            if isinstance(response, CanonicalResponse):
                if response.failed:
                    continue
                unit_status = response.value
                if unit_status is None:
                    logger.error(
                        f"unit '{unit_api.hostname}' ({unit_api.ipaddr}) returned None, ignoring"
                    )
                    continue

                if unit_status["operational"]:
                    operational_unit_apis.append(unit_apis[i])
                    logger.info(
                        f"unit '{unit_api.hostname}' ({unit_api.ipaddr}), operational"
                    )
                else:
                    if OperatingMode.production_mode:
                        logger.info(
                            f"unit '{unit_api.hostname}' ({unit_api.ipaddr}), "
                            + f"not operational: {unit_status['why_not_operational']}"
                        )
                    else:
                        operational_unit_apis.append(unit_apis[i])
                        logger.info(
                            f"using non-operational unit '{unit_api.hostname}' ({unit_api.ipaddr}), operational"
                        )

        if len(operational_unit_apis) == 0:
            if OperatingMode.production_mode:
                self.end_activity(AssignmentActivities.Probing)
                self.end_activity(AssignmentActivities.Executing)
                self.terminate(
                    reason="rejected",
                    details=[f"no operational units (quorum: {self.quorum})"],
                )
                return
        elif (
            self.quorum is not None
            and len(operational_unit_apis) < self.quorum
            and OperatingMode.production_mode
        ):
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            self.terminate(
                reason="rejected",
                details=[
                    f"only {len(operational_unit_apis)} operational "
                    + f"units (quorum: {self.quorum})"
                ],
            )
            return
        logger.info(
            f"continuing with {len(operational_unit_apis)} unit(s) "
            + f"(instead of {self.quorum}), operating in 'debug' mode"
        )

        if isinstance(spec_response, CanonicalResponse):
            if spec_response and spec_response.failed:
                self.end_activity(AssignmentActivities.Probing)
                self.end_activity(AssignmentActivities.Executing)
                self.terminate(
                    reason="rejected",
                    details=[
                        f"cannot talk to spec '{self.spec_api.hostname}' ({self.spec_api.ipaddr}) "
                        + f"(errors: {spec_response.errors})"
                    ],
                )
                return

            spec_status = spec_response.value
            if spec_status and not spec_status.operational:
                if OperatingMode.production_mode:
                    self.end_activity(AssignmentActivities.Probing)
                    self.end_activity(AssignmentActivities.Executing)
                    self.terminate(
                        reason="rejected",
                        details=[
                            f"spec is not operational {spec_status.why_not_operational}"
                        ],
                    )
                    return
                else:
                    logger.info(
                        "continuing with non-operational spec, operating in 'debug' mode"
                    )

        elif isinstance(spec_response, BaseException):
            if OperatingMode.production_mode:
                self.end_activity(AssignmentActivities.Probing)
                self.end_activity(AssignmentActivities.Executing)
                self.terminate(
                    reason="failed",
                    details=[
                        f"exception when getting status from spec {spec_response=}"
                    ],
                )
                return
            else:
                logger.info(
                    "continuing with non-operational spec, operating in 'debug' mode"
                )

        self.end_activity(AssignmentActivities.Probing)

        # Phase #2: we have a quorum of responding units and a responding spec, we can dispatch the assignments
        self.start_activity(AssignmentActivities.Dispatching)
        assignment_tasks = []
        for operational_unit_api in operational_unit_apis:
            for unit_assignment in self.remote_unit_assignments:
                if operational_unit_api.ipaddr == unit_assignment.ipaddr:
                    assignment_tasks.append(
                        self.api_coroutine(
                            operational_unit_api,
                            method="PUT",
                            sub_url="execute_assignment",
                            _json=unit_assignment.assignment.model_dump(),
                        )
                    )
                    break
        canonical_unit_responses: list[GatherResponse] = await asyncio.gather(
            *assignment_tasks, return_exceptions=True
        )
        self.end_activity(AssignmentActivities.Dispatching)

        for i, canonical_response in enumerate(canonical_unit_responses):
            try:
                if isinstance(canonical_response, CanonicalResponse):
                    if canonical_response.succeeded:
                        self.commited_unit_apis.append(operational_unit_apis[i])
                    else:
                        canonical_response.log(
                            _logger=logger,
                            label=f"{operational_unit_apis[i].hostname} "
                            + f"({operational_unit_apis[i].ipaddr})",
                        )
                elif isinstance(canonical_response, BaseException):
                    logger.error(
                        f"exception response from {operational_unit_apis[i].hostname} "
                        + f"({operational_unit_apis[i].ipaddr}): {canonical_response}"
                    )

            except Exception as e:
                logger.error(f"non-canonical response (error: {e}), ignoring!")
                continue

        assert self.quorum is not None, (
            "task.quorum should not be None, it should be set in the task definition"
        )

        n_committed = len(self.commited_unit_apis)
        if n_committed == 0:
            self.terminate(
                reason="rejected",
                details=[f"no committed units (quorum: {self.quorum})"],
            )
            self.end_activity(AssignmentActivities.Dispatching)
            self.end_activity(AssignmentActivities.Executing)
            return
        elif n_committed < self.quorum:
            if OperatingMode.production_mode:
                self.terminate(
                    reason="rejected",
                    details=[f"only {n_committed} units (quorum: {self.quorum})"],
                )
                self.end_activity(AssignmentActivities.Dispatching)
                self.end_activity(AssignmentActivities.Executing)
                return
        else:
            logger.info(
                f"continuing with only {n_committed} 'committed_units' "
                + f"(instead of {self.quorum}) (operating in 'debug' mode)"
            )

        self.add_event(
            EventModel(
                what="submitted",
                details=[
                    f"committed_units: {[api.hostname for api in self.commited_unit_apis]}"
                ],
            )
        )

        # the units are committed to their assignments, now wait for them to reach 'guiding'
        start = datetime.datetime.now()
        reached_guiding = False
        self.start_activity(AssignmentActivities.WaitingForGuiding)

        assert self.timeout_to_guiding is not None, (
            "task.timeout_to_guiding should not be None, "
            "it should be set in the task definition"
        )

        while (datetime.datetime.now() - start).seconds < self.timeout_to_guiding:
            time.sleep(20)
            responses = await self.fetch_statuses(self.commited_unit_apis)

            canonical_responses: list[CanonicalResponse] = [
                response
                for response in responses
                if isinstance(response, CanonicalResponse)
            ]

            statuses: list[ComponentStatus] = [
                response.value
                for response in canonical_responses
                if response.value is not None
                and isinstance(response.value, ComponentStatus)
            ]

            if all(
                [(status.activities & UnitActivities.Guiding) for status in statuses]
            ):
                logger.info(
                    f"all commited units ({[f'{u.hostname} ({u.ipaddr})' for u in self.commited_unit_apis]}) "
                    + "have reached 'Guiding'"
                )
                reached_guiding = True
                break
        self.end_activity(AssignmentActivities.WaitingForGuiding)

        if not reached_guiding:
            self.terminate(
                reason="failed",
                details=[
                    f"did not reach 'guiding' within {self.timeout_to_guiding} seconds"
                ],
            )
            self.end_activity(AssignmentActivities.Executing)
            await self.abort()
            return

        self.start_activity(AssignmentActivities.ExposingSpec)

        # get (again) the spectrograph's status and make sure it is operational and not busy
        status = await self.get_spec_status()
        assert status is not None, "status should not be None"

        if self.production and not status.operational:
            logger.error("spectrograph became non-operational, aborting!")
            self.end_activity(AssignmentActivities.Executing)
            await self.abort()
            return

        if status.activities != Activities.Idle:
            logger.error(
                f"spectrograph is busy (activities={status.activities}), aborting!"
            )
            self.end_activity(AssignmentActivities.Executing)
            await self.abort()
            return

        assert self.remote_spec_assignment is not None, (
            "spec_assignment should not be None"
        )
        # send the assignment to the spec

        # logger.info(f"spec_assignment ({type(self.spec_assignment)}):\n" + self.spec_assignment.model_dump_json(indent=2))
        canonical_response = await self.spec_api.put(
            method="execute_assignment", json=self.remote_spec_assignment.model_dump()
        )

        if not canonical_response.succeeded:
            canonical_response.log(_logger=logger, label="spec rejected assignment")
            await self.abort()
            self.end_activity(AssignmentActivities.Executing)
            return

        self.start_activity(AssignmentActivities.WaitingForSpecDone)
        while True:
            time.sleep(20)
            spec_status: ComponentStatus | None = await self.get_spec_status()
            assert spec_status is not None, "spec_status should not be None"

            if not spec_status.operational:
                for err in spec_status.why_not_operational:
                    logger.error(f"spec not operational: {err}")
                if OperatingMode.production_mode:
                    await self.abort()
                    self.end_activity(AssignmentActivities.WaitingForSpecDone)
                    self.end_activity(AssignmentActivities.Executing)
                    return
                else:
                    logger.info(
                        "ignoring non-operational spec (operating in 'debug' mode)"
                    )

            logger.info("execute: " + json.dumps(spec_status, indent=2))
            if spec_status.activities == Activities.Idle:
                logger.info("spec is Idle")
                self.end_activity(AssignmentActivities.WaitingForSpecDone)
                self.end_activity(AssignmentActivities.Executing)
                break
            else:
                logger.info(
                    f"spec is busy: activities: {spec_status.activities} "
                    + f"({spec_status.activities_verbal})"
                )

    async def abort(self):
        self.start_activity(AssignmentActivities.Aborting)
        tasks = [
            self.api_coroutine(unit_api, method="GET", sub_url="abort")
            for unit_api in self.commited_unit_apis
        ]
        if self.spec_api:
            tasks.append(
                self.api_coroutine(self.spec_api, method="GET", sub_url="abort")
            )
        self.end_activity(AssignmentActivities.Aborting)


if __name__ == "__main__":
    import json
    import sys
    import traceback

    if len(sys.argv) < 2:
        print("Usage: python common/models/plans.py <plan-file.toml>")
        sys.exit(1)

    toml_path = sys.argv[1]
    try:
        plan = Plan.from_toml_file(toml_path)
        # model_dump() is Pydantic v2 method; fall back to dict() if needed
        try:
            data = plan.model_dump()
        except Exception:
            data = plan.model_dump()
        print(json.dumps(data, indent=2, default=str))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
