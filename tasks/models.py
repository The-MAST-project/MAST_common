import asyncio
import datetime
import json
import logging
import os.path
import shutil
import socket
import time
from collections.abc import Container
from copy import deepcopy
from pathlib import Path
from typing import Literal

import tomlkit
import tomlkit.exceptions
import ulid
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field

from common.activities import Activities, AssignmentActivities, UnitActivities
from common.api import ApiDomain, SpecApi, UnitApi
from common.canonical import CanonicalResponse
from common.config import Config
from common.deep import deep_dict_update
from common.interfaces.components import ComponentStatus
from common.mast_logging import init_log
from common.models.assignments import (
    Initiator,
    SpectrographAssignmentModel,
    TargetModel,
    TaskSettingsModel,
    TransmittedAssignment,
    UnitAssignmentModel,
)
from common.models.constraints import ConstraintsModel
from common.models.spectrographs import SpectrographModel
from common.parsers import parse_units
from common.spec import DeepspecBands
from common.utils import OperatingMode, function_name

GatherResponse = CanonicalResponse | BaseException | None

logger = logging.getLogger("tasks")
init_log(logger)


def make_spec_model(spec_doc: dict) -> SpectrographModel | None:
    """
    Accumulates a dictionary by combining:
    - a TOML-derived dictionary (parameter) which contains the user's task description
    - defaults from the configuration database

    The resulting dictionary is fully populated, i.e. ALL the expected fields
      have a value (either from the task, or the defaults)

    :param spec_doc: a dictionary from a TOML model
    :return: a spectrograph model built from the accumulated dictionary
    """
    if "instrument" not in spec_doc:
        logger.error(f"missing 'instrument' in {spec_doc=}")
        return None
    instrument = spec_doc["instrument"]
    if instrument not in ["highspec", "deepspec"]:
        logger.error(f"bad '{instrument=}', must be either 'deepspec' or 'highspec")
        return None

    defaults = Config().get_specs()
    calibration_settings = {
        "lamp_on": spec_doc.get("lamp_on", False),
        "filter": spec_doc.get("filter"),
    }

    if instrument == "highspec":
        camera_settings = deepcopy(defaults.highspec.settings)
        if "camera" in spec_doc:
            # deep_dict_update(camera_settings, spec_doc["camera"])
            deepcopy(camera_settings, spec_doc["camera"])
        exposure_duration = defaults.highspec.settings.exposure_duration
        number_of_exposures = defaults.highspec.settings.number_of_exposures

        # propagate 'exposure_duration' and 'number_of_exposures' to the camera settings
        camera_settings.exposure_duration = exposure_duration
        camera_settings.number_of_exposures = number_of_exposures

        new_spec_dict = {
            "instrument": instrument,
            "calibration": calibration_settings,
            "exposure_duration": exposure_duration,
            "number_of_exposures": number_of_exposures,
            "spec": {
                "instrument": instrument,
                "disperser": spec_doc["disperser"],
                "camera": camera_settings,
            },
        }

    else:
        default_common_settings = defaults.deepspec["common"].settings
        assert default_common_settings is not None, (
            "empty default_camera_settings for Deepspec"
        )

        new_spec_dict = {
            "instrument": instrument,
            "calibration": calibration_settings,
            "exposure_duration": (
                spec_doc.get(
                    "exposure_duration", default_common_settings.exposure_duration
                )
            ),
            "number_of_exposures": (
                spec_doc.get(
                    "number_of_exposures", default_common_settings.number_of_exposures
                )
            ),
            "spec": {
                "instrument": instrument,
                "exposure_duration": (
                    spec_doc.get(
                        "exposure_duration", default_common_settings.exposure_duration
                    )
                ),
                "number_of_exposures": (
                    spec_doc.get(
                        "number_of_exposures",
                        default_common_settings.number_of_exposures,
                    )
                ),
                "camera": {},
            },
        }
        common_camera_settings = deepcopy(default_common_settings)
        # propagate 'exposure_duration' and 'number_of_exposures' to the camera settings
        common_camera_settings.exposure_duration = new_spec_dict["spec"][
            "exposure_duration"
        ]
        common_camera_settings.number_of_exposures = new_spec_dict["spec"][
            "number_of_exposures"
        ]

        # get band-specific camera settings
        for band in DeepspecBands.__args__:
            band_conf = deepcopy(common_camera_settings)
            if "camera" in spec_doc and band in spec_doc["camera"]:
                deep_dict_update(band_conf.model_dump(), spec_doc["camera"][band])

            new_spec_dict["spec"]["camera"][band] = band_conf

    new_spec_dict["instrument"] = instrument

    # logger.info("new_spec_dict:\n" + json.dumps(new_spec_dict, indent=2))
    try:
        spectrograph_model = SpectrographModel(**new_spec_dict)
    except ValidationError as e:
        logger.error("====== ValidationError(s) =======\n")
        for err in e.errors():
            logger.error(f"[ERR] {json.dumps(err, indent=2)}\n")
        raise
    return spectrograph_model


class EventModel(BaseModel):
    what: str | None = None
    details: list[str] | None = None

    @computed_field
    @property
    def when(self) -> str:
        return datetime.datetime.now(datetime.UTC).isoformat()


class TaskModel(BaseModel, Activities):
    """
    A task ready for execution (already planned and scheduled)
    """

    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,  # Allow non-Pydantic types like UnitApi
    )

    # Core fields
    unit: dict[str, TargetModel]  # indexed by unit name, per-unit target assignment(s)
    task: TaskSettingsModel  # general task stuff (ulid, etc.)
    events: list[EventModel] | None = None  # things that happened to this task
    constraints: ConstraintsModel | None = None
    commited_unit_apis: list[UnitApi] = []  # the units that committed to this task

    # File and runtime fields
    toml_file: str | None = Field(
        default=None, description="Path to the TOML file containing the task definition"
    )
    activities: int = Field(default=0, description="Current activities bitmask")
    timings: dict = Field(
        default_factory=dict, description="Timing information for task execution"
    )
    unit_assignments: list[TransmittedAssignment] = Field(
        default_factory=list,
        description="List of unit assignments",
        alias="unit_assignments",
    )
    spec_assignment: TransmittedAssignment | None = Field(
        default=None,
        description="Spectrograph assignment if any",
        alias="spec_assignment",
    )
    spec_api: SpecApi | None = Field(
        default=None, description="API client for spectrograph communication"
    )

    @computed_field
    @property
    def remote_unit_assignments(self) -> list[TransmittedAssignment]:
        ret: list[TransmittedAssignment] = []
        initiator = Initiator.local_machine()
        for key in list(self.unit.keys()):
            unit_assignment: UnitAssignmentModel = UnitAssignmentModel(
                initiator=initiator,
                target=TargetModel(ra=self.unit[key].ra, dec=self.unit[key].dec),
                task=self.task,
            )

            units_specifier = parse_units(key)
            if units_specifier:
                units = TransmittedAssignment.from_units_specifier(
                    units_specifier, unit_assignment
                )
                if units:
                    ret += units
        return ret

    @computed_field
    @property
    def remote_spec_assignment(self) -> TransmittedAssignment | None:
        local_site = Config().local_site
        hostname = local_site.spec_host
        if hostname is None:
            return
        fqdn = f"{hostname}.{local_site.domain}"
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            ipaddr = None

        spec_model = make_spec_model(self.model_extra.get("spec"))  # type: ignore
        if not spec_model:
            logger.error("cannot create a spectrograph model, aborting!")
            return None
        if not spec_model.instrument:
            logger.error("spectrograph model has no instrument, aborting!")
            return None

        initiator = Initiator.local_machine()
        try:
            spec_assignment = SpectrographAssignmentModel(
                instrument=spec_model.instrument,
                initiator=initiator,
                task=self.task,
                spec=spec_model,
            )
        except ValidationError as e:
            for err in e.errors():
                logger.error(f"ERR:\n  {err}")
            raise
        return TransmittedAssignment(
            hostname=hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=spec_assignment
        )

    @classmethod
    def from_toml_file(cls, toml_file: str):
        """
        Loads a TOML model from an assigned-task file and ensures it has required fields.

        Args:
            toml_file: Path to a TOML format task definition file.

        Returns:
            TaskModel: The loaded and validated task model.

        Raises:
            FileNotFoundError: If the TOML file does not exist
            tomlkit.exceptions.TOMLKitError: If the TOML file is invalid
            OSError: If there are file read/write errors
        """
        toml_path = Path(toml_file)
        if not toml_path.exists():
            raise FileNotFoundError(f"Task file not found: {toml_file}") from None

        try:
            with open(toml_file) as fp:
                toml_doc = tomlkit.load(fp)
        except tomlkit.exceptions.TOMLKitError as e:
            raise tomlkit.exceptions.TOMLKitError(
                f"Invalid TOML file {toml_file}: {str(e)}"
            ) from e
        except OSError as e:
            raise OSError(f"Failed to read task file {toml_file}: {str(e)}") from e

        # Initialize or validate the task section
        if "task" not in toml_doc:
            toml_doc["task"] = tomlkit.table()

        # Ensure required fields exist with valid values
        task_section = toml_doc["task"]
        assert isinstance(task_section, Container)
        modified = False

        # Generate ULID if needed
        if "ulid" not in task_section:
            task_section["ulid"] = str(ulid.new()).lower()
            modified = True

        # Update file path if needed
        real_path = str(Path(os.path.realpath(toml_file)).as_posix())
        if (
            "file" not in task_section
            or not task_section["file"]
            or task_section["file"] != real_path
        ):
            task_section["file"] = real_path
            modified = True

        # Write back if any changes were made
        if modified:
            try:
                with open(toml_file, "w") as f:
                    f.write(tomlkit.dumps(toml_doc))
            except OSError as e:
                raise OSError(
                    f"Failed to update task file {toml_file}: {str(e)}"
                ) from e

        # Create the task model with all required fields
        new_task = TaskModel(
            **toml_doc.__dict__
            # toml_file=toml_file,
            # activities=0,
            # timings={},
            # commited_unit_apis=[],
            # unit_assignments=[],
            # spec_assignment=None,
            # spec_api=None,
        )
        if "events" not in toml_doc:
            new_task.add_event(EventModel(what="created"))

        return new_task

    def __repr__(self):
        return f"<AssignedTask>(ulid='{self.task.ulid}')"

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
    ) -> tuple[list[GatherResponse], GatherResponse | None]:
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

        logger.error(f"terminating task '{self.task.ulid}', {reason=}, {details=}")

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
                f"moved task '{self.task.ulid}' from {str(current_path)} to {str(new_path)}"
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
        self.spec_api = SpecApi(Config().local_site.name)
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
        if self.task.quorum is not None and n_detected < self.task.quorum:
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            if n_detected == 0:
                self.terminate(
                    reason="rejected",
                    details=[
                        f"no units quorum, no units were detected (required: {self.task.quorum})"
                    ],
                )
            else:
                self.terminate(
                    reason="rejected",
                    details=[
                        f"no units quorum, detected only {n_detected} "
                        + f"({[unit_api.hostname for unit_api in detected_unit_apis]}), "
                        + f"required {self.task.quorum}"
                    ],
                )
            return
        logger.info(
            f"'detected_units' quorum achieved ({n_detected} detected out of {self.task.quorum} required)"
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
                    details=[f"no operational units (quorum: {self.task.quorum})"],
                )
                return
        elif (
            self.task.quorum is not None
            and len(operational_unit_apis) < self.task.quorum
            and OperatingMode.production_mode
        ):
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            self.terminate(
                reason="rejected",
                details=[
                    f"only {len(operational_unit_apis)} operational "
                    + f"units (quorum: {self.task.quorum})"
                ],
            )
            return
        logger.info(
            f"continuing with {len(operational_unit_apis)} unit(s) "
            + f"(instead of {self.task.quorum}), operating in 'debug' mode"
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

        assert self.task.quorum is not None, (
            "task.quorum should not be None, it should be set in the task definition"
        )

        n_committed = len(self.commited_unit_apis)
        if n_committed == 0:
            self.terminate(
                reason="rejected",
                details=[f"no committed units (quorum: {self.task.quorum})"],
            )
            self.end_activity(AssignmentActivities.Dispatching)
            self.end_activity(AssignmentActivities.Executing)
            return
        elif n_committed < self.task.quorum:
            if OperatingMode.production_mode:
                self.terminate(
                    reason="rejected",
                    details=[f"only {n_committed} units (quorum: {self.task.quorum})"],
                )
                self.end_activity(AssignmentActivities.Dispatching)
                self.end_activity(AssignmentActivities.Executing)
                return
        else:
            logger.info(
                f"continuing with only {n_committed} 'committed_units' "
                + f"(instead of {self.task.quorum}) (operating in 'debug' mode)"
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

        assert self.task.timeout_to_guiding is not None, (
            "task.timeout_to_guiding should not be None, "
            "it should be set in the task definition"
        )

        while (datetime.datetime.now() - start).seconds < self.task.timeout_to_guiding:
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
                    f"did not reach 'guiding' within {self.task.timeout_to_guiding} seconds"
                ],
            )
            self.end_activity(AssignmentActivities.Executing)
            await self.abort()
            return

        self.start_activity(AssignmentActivities.ExposingSpec)

        # get (again) the spectrograph's status and make sure it is operational and not busy
        status = await self.get_spec_status()
        assert status is not None, "status should not be None"

        if self.task.production and not status.operational:
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


class TaskAcquisitionPathNotification(BaseModel):
    """
    Sent to the controller by:
    - the units, as soon as they know the path of either an 'autofocus' or 'acquisition' folder
    - the spec, as soon as it has the path of the acquisition
    """

    initiator: Initiator
    task_id: str
    src: str
    link: Literal["autofocus", "acquisition", "deepspec", "highspec", "spec"]


async def main():
    # task_file = '/Storage/mast-share/MAST/tasks/assigned/TSK_assigned_highspec_task.toml'
    task_file = (
        "/Storage/mast-share/MAST/tasks/assigned/TSK_assigned_deepspec_task.toml"
    )
    try:
        assigned_task: TaskModel = TaskModel.from_toml_file(task_file)
    except ValidationError as e:
        for err in e.errors():
            logger.error(err)
        raise

    remote_assignment = assigned_task.remote_spec_assignment
    if not remote_assignment:
        raise Exception(
            f"task '{assigned_task.task.ulid}' has no spec assignment, cannot continue"
        )

    # Type assertion to help Pylance understand the spec type
    assert isinstance(remote_assignment.assignment, SpectrographAssignmentModel)
    logger.info("remote assignment: " + remote_assignment.model_dump_json(indent=2))

    spec_api = SpecApi()
    logger.info(
        f"sending task '{remote_assignment.assignment.task.ulid}' "
        + f"({remote_assignment.assignment.spec.instrument}) to '{spec_api.hostname}' ({spec_api.ipaddr})"
    )
    canonical_response = await spec_api.put(
        method="execute_assignment", json=remote_assignment.model_dump()
    )
    if canonical_response.succeeded:
        logger.info(
            f"[{spec_api.ipaddr}] ACCEPTED task '{remote_assignment.assignment.task.ulid}'"
        )
    else:
        logger.error(
            f"[{spec_api.ipaddr}] REJECTED task '{remote_assignment.assignment.task.ulid}'"
        )
        if canonical_response.errors:
            for err in canonical_response.errors:
                logger.error(f"[{spec_api.ipaddr}] {err}")


if __name__ == "__main__":
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
