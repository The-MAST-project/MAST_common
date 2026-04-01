from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import shutil
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import tomlkit
import ulid
from pydantic import BaseModel, Field, ValidationError
from pydantic.config import ConfigDict

from common.activities import Activities, PlanActivities, UnitActivities
from common.mast_logging import init_log
from common.models.constraints import ConstraintsModel
from common.models.events import EventModel
from common.models.spectrographs import SpectrographModel
from common.models.targets import Target
from common.utils import function_name

if TYPE_CHECKING:
    from common.api import SpecApi, UnitApi
    from common.canonical import CanonicalResponse
    from common.interfaces.components import ComponentStatus
    from common.models.assignments import Manifest
    from common.tasks.models import GatherResponse

logger = logging.Logger("mast." + __name__)
init_log(logger)


class Plan(BaseModel, Activities):
    target: Target
    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,
    )

    ulid: str | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    full_path: Path | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    owner: str | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Owner",
                "widget": "user",
                "editable": False,
                "summary": True,
            },
            "searchable": "exact",
        },
    )
    mockup: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Mockup",
                "widget": "checkbox",
                "hidden": True,
                "tooltip": "If true, the plan will not be executed but only go through the scheduling phase (for testing and debugging)",
            },
            "searchable": True,
        },
    )
    merit: int | None = Field(
        default=1,
        json_schema_extra={
            "ui": {
                "label": "Merit",
                "widget": "select",
                "options": list(range(1, 11)),
                "summary": True,
            },
            "searchable": "exact",
        },
    )
    timeout_to_guiding: float | None = Field(
        default=600,
        gt=0,
        le=600,
        json_schema_extra={
            "ui": {
                "label": "Timeout to Guiding",
                "widget": "number",
                "unit": "seconds",
                "required_capabilities": ["can_manage_plans"],
            },
        },
    )
    autofocus: bool | None = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Autofocus",
                "widget": "checkbox",
                "tooltip": "Perform autofocus before acquisition",
            },
            "searchable": True,
        },
    )
    too: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Target of Opportunity",
                "widget": "checkbox",
                "summary": True,
                "required_capabilities": ["can_manage_plans"],
            },
            "searchable": True,
        },
    )
    approved: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Approved",
                "widget": "checkbox",
                "editable": False,
                "required_capabilities": ["can_manage_plans"],
                "hidden": True,
            },
            "searchable": True,
        },
    )
    spec_assignment: SpectrographModel | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Spectrograph",
            }
        },
    )
    run_folder: str | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    production: bool | None = Field(
        default=True,
        json_schema_extra={
            "ui": {
                "label": "Production",
                "widget": "checkbox",
                "required_capabilities": ["can_manage_plans"],
                "tooltip": "Disable to relax availability checks (testing only)",
                "hidden": True,
            }
        },
    )
    events: list[EventModel] | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    constraints: ConstraintsModel | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Constraints",
            }
        },
    )
    commited_unit_apis: list[Any] = Field(
        default_factory=list,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )  # list[UnitApi]
    timings: dict = Field(
        default_factory=dict,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    requested_units: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "ui": {
                "label": "Requested Units",
                "widget": "text",
                "tooltip": "Comma-separated unit names, e.g. <b>mast01,mast02</b><br>"
                + "&nbsp;If specified, the scheduler will try to allocate these specific units (if available), <br>"
                + "&nbsp;otherwise it will choose from available units",
            }
        },
    )
    allocated_units: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "ui": {
                "label": "Allocated Units",
                "editable": False,
                "required_capabilities": ["can_manage_plans"],
                "tooltip": "Units allocated by scheduler",
            }
        },
    )
    quorum: int = Field(
        default=1,
        json_schema_extra={
            "ui": {
                "label": "Quorum",
                "widget": "number",
                "tooltip": "Minimum operational units required for the plan to proceed",
                "required_capabilities": ["can_manage_plans"],
            }
        },
    )
    unit_apis: list[Any] = Field(
        default=[],
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    spec_api: Any | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    terminated: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )

    @property
    def remote_unit_assignments(self) -> list[Manifest]:
        from common.models.assignments import (
            Initiator,
            Manifest,
            UnitAssignment,
        )
        from common.parsers import parse_units

        ret: list[Manifest] = []
        initiator = Initiator.local_machine()
        for unit_name in self.allocated_units or []:
            unit_assignment: UnitAssignment = UnitAssignment(
                initiator=initiator,
                plan=self,
            )

            units_specifier = parse_units(unit_name)
            if units_specifier:
                units = Manifest.from_units_specifier(units_specifier, unit_assignment)
                if units:
                    ret += units
        return ret

    @property
    def remote_spec_assignment(self) -> Manifest | None:
        from common.config import Config
        from common.models.assignments import (
            Initiator,
            Manifest,
            SpectrographAssignment,
        )

        local_site = Config().local_site
        assert local_site is not None
        spec_hostname = local_site.spec_host
        if spec_hostname is None:
            return
        fqdn = f"{spec_hostname}.{local_site.domain}"
        try:
            ipaddr = socket.gethostbyname(spec_hostname)
        except socket.gaierror:
            ipaddr = None

        if not self.spec_assignment:
            logger.error("cannot create a spectrograph model, aborting!")
            return None
        if not self.spec_assignment.instrument:
            logger.error("spectrograph model has no instrument, aborting!")
            return None

        initiator = Initiator.local_machine()
        try:
            spec_assignment = SpectrographAssignment(
                instrument=self.spec_assignment.instrument,
                initiator=initiator,
                plan=self,
                spec=self.spec_assignment,
            )
        except ValidationError as e:
            for err in e.errors():
                logger.error(f"ERR:\n  {err}")
            raise

        return Manifest(
            hostname=spec_hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=spec_assignment
        )

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
        new_plan = Plan(**toml_doc)  # type: ignore  — ValidationError propagates to caller

        return new_plan

    def __repr__(self):
        return f"<Plan>(ulid='{self.ulid}')"

    @staticmethod
    async def api_coroutine(
        api, method: str, sub_url: str, data=None, _json: dict | None = None
    ):
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
    ) -> tuple[list[GatherResponse], Any | None]:
        """Asynchronously fetch status information from multiple units and optionally a spectrograph."""
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

    async def get_spec_status(self) -> Any | None:  # ComponentStatus | None
        if not self.spec_api:
            raise Exception(f"{function_name()}: spec_api is None")

        canonical_response = await self.spec_api.get(method="status")
        if not canonical_response.succeeded:
            canonical_response.log(_logger=logger, label="spec")
            await self.abort()
            self.end_activity(PlanActivities.Exposing)
            return None

        return canonical_response.value

    def prepare(self, controller):
        self.controller = controller
        self.sites_conf = controller.sites_conf

    async def terminate(
        self, reason: Literal["failed", "rejected", "completed"], details: list[str]
    ):
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
        self.end_activity(PlanActivities.Executing)
        self.terminated = True

    def add_event(self, event: EventModel):
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

    async def probe(self):
        """Checks if the allocated components (units and spectrograph) are available and operational."""

        self.start_activity(PlanActivities.Probing)
        if not self.unit_apis or not self.spec_api:
            self.end_activity(PlanActivities.Probing)
            await self.terminate(
                reason="rejected",
                details=["no units assigned to this task"],
            )
            return
        canonical_unit_responses, spec_response = await self.fetch_statuses(
            self.unit_apis, self.spec_api
        )

        # see what units respond at all
        detected_unit_apis = [
            unit_api for unit_api in self.unit_apis if unit_api.detected
        ]
        n_detected = len(detected_unit_apis)
        if self.quorum is not None and n_detected < self.quorum:
            self.end_activity(PlanActivities.Probing)
            if n_detected == 0:
                await self.terminate(
                    reason="rejected",
                    details=[
                        f"no units quorum, no units were detected (required: {self.quorum})"
                    ],
                )
            else:
                await self.terminate(
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
            self.end_activity(PlanActivities.Probing)
            await self.terminate(reason="rejected", details=["spec not detected"])
            return

        # enough units were detected (they answered to API calls), now check if they are operational
        self.operational_unit_apis = []
        for i, response in enumerate(canonical_unit_responses):
            unit_api = self.unit_apis[i]

            from common.models.statuses import UnitStatus

            if isinstance(response, CanonicalResponse):
                if response.failed:
                    continue
                unit_status = cast(UnitStatus, response.value)
                if unit_status is None:
                    logger.error(
                        f"unit '{unit_api.hostname}' ({unit_api.ipaddr}) returned None, ignoring"
                    )
                    continue

                if unit_status.operational:
                    self.operational_unit_apis.append(self.unit_apis[i])
                    logger.info(
                        f"unit '{unit_api.hostname}' ({unit_api.ipaddr}), operational"
                    )
                else:
                    logger.info(
                        f"unit '{unit_api.hostname}' ({unit_api.ipaddr}), "
                        + f"not operational: {unit_status.why_not_operational}"
                    )

        if len(self.operational_unit_apis) == 0:
            self.end_activity(PlanActivities.Probing)
            await self.terminate(
                reason="rejected",
                details=[f"no operational units (quorum: {self.quorum})"],
            )
            return
        elif self.quorum is not None:
            if len(self.operational_unit_apis) < self.quorum:
                self.end_activity(PlanActivities.Probing)
                await self.terminate(
                    reason="rejected",
                    details=[
                        f"only {len(self.operational_unit_apis)} operational "
                        + f"units (quorum: {self.quorum})"
                    ],
                )
                return

        logger.info(
            f"continuing with {len(self.operational_unit_apis)} unit(s) (quorum: {self.quorum})"
        )

        if isinstance(spec_response, CanonicalResponse):
            if spec_response and spec_response.failed:
                self.end_activity(PlanActivities.Probing)
                await self.terminate(
                    reason="rejected",
                    details=[
                        f"cannot talk to spec '{self.spec_api.hostname}' ({self.spec_api.ipaddr}) "
                        + f"(errors: {spec_response.errors})"
                    ],
                )
                return

            assert spec_response is not None
            spec_status = spec_response.value
            if spec_status and not spec_status.operational:
                self.end_activity(PlanActivities.Probing)
                await self.terminate(
                    reason="rejected",
                    details=[
                        f"spec is not operational {spec_status.why_not_operational}"
                    ],
                )
                return

        elif isinstance(spec_response, BaseException):
            self.end_activity(PlanActivities.Probing)
            await self.terminate(
                reason="failed",
                details=[f"exception when getting status from spec {spec_response=}"],
            )
            return

        self.end_activity(PlanActivities.Probing)

    async def dispatch(self):
        """We have a quorum of responding units and a responding spec, we can dispatch the assignments"""

        self.start_activity(PlanActivities.Dispatching)
        assignment_tasks = []
        for operational_unit_api in self.operational_unit_apis:
            for unit_assignment in self.remote_unit_assignments:
                if unit_assignment.assignment is None:
                    logger.error(
                        f"unit_assignment.assignment is None for unit '{unit_assignment.hostname}'"
                    )
                    continue
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
        self.end_activity(PlanActivities.Dispatching)

        for i, canonical_response in enumerate(canonical_unit_responses):
            try:
                if isinstance(canonical_response, CanonicalResponse):
                    if canonical_response.succeeded:
                        self.commited_unit_apis.append(self.operational_unit_apis[i])
                    else:
                        canonical_response.log(
                            _logger=logger,
                            label=f"{self.operational_unit_apis[i].hostname} "
                            + f"({self.operational_unit_apis[i].ipaddr})",
                        )
                elif isinstance(canonical_response, BaseException):
                    logger.error(
                        f"exception response from {self.operational_unit_apis[i].hostname} "
                        + f"({self.operational_unit_apis[i].ipaddr}): {canonical_response}"
                    )

            except Exception as e:
                logger.error(f"non-canonical response (error: {e}), ignoring!")
                continue

        n_committed = len(self.commited_unit_apis)
        if n_committed == 0:
            self.end_activity(PlanActivities.Dispatching)
            await self.terminate(
                reason="rejected",
                details=[f"no committed units (quorum: {self.quorum})"],
            )
            return
        elif self.quorum is not None:
            if n_committed < self.quorum:
                self.end_activity(PlanActivities.Dispatching)
                await self.terminate(
                    reason="rejected",
                    details=[f"only {n_committed} units (quorum: {self.quorum})"],
                )
                return

        self.add_event(
            EventModel(
                what="dispatched",
                details=[
                    f"committed_units: {[api.hostname for api in self.commited_unit_apis]}"
                ],
            )
        )

    async def expose(self):
        """Tell the spectrograph to execute the assignment and wait for it to finish"""

        self.start_activity(PlanActivities.Exposing)

        # get (again) the spectrograph's status and make sure it is operational and not busy
        spec_status = await self.get_spec_status()
        assert spec_status is not None, "spec_status should not be None"

        if not spec_status.operational:
            await self.terminate(
                reason="failed",
                details=[f"spec not operational: {spec_status.why_not_operational}"],
            )
            return

        if spec_status.activities != Activities.Idle:
            await self.terminate(
                reason="failed",
                details=[
                    f"spectrograph is busy (activities={spec_status.activities}), aborting!"
                ],
            )
            return

        assert self.remote_spec_assignment is not None, (
            "spec_assignment should not be None"
        )

        assert self.spec_api is not None, "spec_api should not be None"
        canonical_response = await self.spec_api.put(
            method="execute_assignment", json=self.remote_spec_assignment.model_dump()
        )

        if not canonical_response.succeeded:
            await self.terminate(
                reason="failed",
                details=[
                    f"failed to send assignment to spectrograph: {canonical_response.errors}"
                ],
            )
            return

    async def wait_for_guiding(self):
        """Units are committed to their assignments, now wait for them to reach 'guiding'"""

        start = datetime.datetime.now()
        reached_guiding = False
        self.start_activity(PlanActivities.WaitingForGuiding)

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
        self.end_activity(PlanActivities.WaitingForGuiding)

        if not reached_guiding:
            await self.terminate(
                reason="failed",
                details=[
                    f"did not reach 'guiding' within {self.timeout_to_guiding} seconds"
                ],
            )
            return

    async def wait_for_spec_done(self):
        """Wait for the spectrograph to finish the assignment (go back to idle)"""

        self.start_activity(PlanActivities.WaitingForSpecDone)
        while True:
            time.sleep(20)
            spec_status: ComponentStatus | None = await self.get_spec_status()
            assert spec_status is not None, "spec_status should not be None"

            if not spec_status.operational:
                for err in spec_status.why_not_operational:
                    logger.error(f"spec not operational: {err}")
                self.end_activity(PlanActivities.WaitingForSpecDone)
                await self.terminate(
                    reason="failed",
                    details=[
                        f"spec not operational: {spec_status.why_not_operational}"
                    ],
                )
                return

            logger.info("wait_for_spec_done: " + json.dumps(spec_status, indent=2))
            if spec_status.activities == Activities.Idle:
                logger.info("spec is Idle")
                self.end_activity(PlanActivities.WaitingForSpecDone)
                break
            else:
                logger.info(
                    f"wait_for_spec_done: spec is busy: activities: {spec_status.activities} "
                    + f"({spec_status.activities_verbal})"
                )

        self.end_activity(PlanActivities.WaitingForSpecDone)

    async def execute(self, controller):
        """
        Executes the plan by orchestrating the probing, dispatching, guiding, exposing and waiting for completion phases.
        """
        from common.api import SpecApi, UnitApi
        from common.config import Config

        local_site = Config().local_site
        assert local_site is not None

        self.spec_api = SpecApi(local_site.name)
        self.controller = controller
        unit_apis: list[UnitApi] = []

        for assignment in self.remote_unit_assignments:
            unit_apis.append(UnitApi(ipaddr=assignment.ipaddr))

        self.start_activity(PlanActivities.Executing)

        await self.probe()
        if self.terminated:
            logger.debug(
                f"{function_name()}: terminated after probing, exiting execute()"
            )
            return

        await self.dispatch()
        if self.terminated:
            logger.debug(
                f"{function_name()}: terminated after dispatching, exiting execute()"
            )
            return

        await self.wait_for_guiding()
        if self.terminated:
            logger.debug(
                f"{function_name()}: terminated while waiting for guiding, exiting execute()"
            )
            return

        await self.expose()
        if self.terminated:
            logger.debug(
                f"{function_name()}: terminated while exposing, exiting execute()"
            )
            return

        await self.wait_for_spec_done()
        if self.terminated:
            logger.debug(
                f"{function_name()}: terminated while waiting for spec done, exiting execute()"
            )
            return

        await self.terminate(reason="completed", details=["plan executed successfully"])

    async def abort(self):
        from common.activities import PlanActivities

        self.start_activity(PlanActivities.Aborting)
        tasks = [
            self.api_coroutine(unit_api, method="GET", sub_url="abort")
            for unit_api in self.commited_unit_apis
        ]
        tasks.append(self.api_coroutine(self.spec_api, method="GET", sub_url="abort"))
        self.end_activity(PlanActivities.Aborting)


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
        try:
            data = plan.model_dump()
        except Exception:
            data = plan.model_dump()
        print(json.dumps(data, indent=2, default=str))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
