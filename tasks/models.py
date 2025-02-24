import asyncio
import datetime
import os.path
import shutil
import socket
import sys
import time
import json

import tomlkit
import ulid
from pydantic import BaseModel, ValidationError, computed_field, ConfigDict
import logging

from common.activities import Activities, AssignmentActivities
from common.config import Config
from common.mast_logging import init_log
from common.parsers import parse_units
from typing import Literal, List, Optional, Dict
from common.models.assignments import RemoteAssignment
from common.models.assignments import AssignmentInitiator, TargetAssignmentModel, AssignedTaskSettingsModel, \
    UnitAssignmentModel
from common.spec import DeepspecBands
from common.models.spectrographs import SpectrographModel
from common.models.assignments import SpectrographAssignmentModel
from common.models.constraints import ConstraintsModel
from common.api import UnitApi, SpecApi, ApiDomain
from pathlib import Path
from common.activities import UnitActivities, Timing
from copy import deepcopy

from common.utils import CanonicalResponse, deep_dict_update

logger = logging.getLogger('tasks')
init_log(logger)


def make_spec_model(spec_doc) -> SpectrographModel | None:
    """
    Accumulates a dictionary by combining:
    - a TOML-derived dictionary (parameter) which contains the user's task description
    - defaults from the configuration database

    The resulting dictionary is fully populated, i.e. ALL the expected fields
      have a value (either from the task, or the defaults)

    :param spec_doc: a dictionary from a TOML model
    :return: a spectrograph model built from the accumulated dictionary
    """
    if not 'instrument' in spec_doc:
        logger.error(f"missing 'instrument' in {spec_doc=}")
        return None
    instrument = spec_doc['instrument']
    if instrument not in ['highspec', 'deepspec']:
        logger.error(f"bad '{instrument=}', must be either 'deepspec' or 'highspec")
        return None

    defaults = Config().get_specs()
    calibration_settings = {
        'lamp_on': spec_doc['lamp_on'] if 'lamp_on' in spec_doc else False,
        'filter': spec_doc['filter'] if 'filter' in spec_doc else None,
    }

    if instrument == 'highspec':

        camera_settings: dict = deepcopy(defaults['highspec']['settings'])
        if 'camera' in spec_doc:
            deep_dict_update(camera_settings, spec_doc['camera'])
        exposure_duration = spec_doc['exposure_duration'] if 'exposure_duration' in spec_doc else defaults['highspec']['settings']['exposure_duration']
        number_of_exposures = spec_doc['number_of_exposures'] if 'number_of_exposures' in spec_doc else defaults['highspec']['settings']['number_of_exposures']

        # propagate 'exposure_duration' and 'number_of_exposures' to the camera settings
        camera_settings['exposure_duration'] = exposure_duration
        camera_settings['number_of_exposures'] = number_of_exposures

        new_spec_dict = {
            'instrument': instrument,
            'calibration': calibration_settings,
            'exposure_duration': exposure_duration,
            'number_of_exposures': number_of_exposures,
            'spec': {
                'instrument': instrument,
                'disperser': spec_doc['disperser'],
                'camera': camera_settings
            }
        }

    else:
        default_common_settings = defaults['deepspec']['common']['settings']
        new_spec_dict = {
            'instrument': instrument,
            'calibration': calibration_settings,

            'exposure_duration': spec_doc['exposure_duration'] if 'exposure_duration' in spec_doc else default_common_settings['exposure_duration'],
            'number_of_exposures': spec_doc['number_of_exposures'] if 'number_of_exposures' in spec_doc
                else default_common_settings['number_of_exposures'],

            'spec': {
                'instrument': instrument,
                'exposure_duration': spec_doc['exposure_duration'] if 'exposure_duration' in spec_doc else default_common_settings['exposure_duration'],
                'number_of_exposures': spec_doc['number_of_exposures'] if 'number_of_exposures' in spec_doc
                    else default_common_settings['number_of_exposures'],
                'camera': {}
            }
        }
        common_camera_settings = deepcopy(default_common_settings)

        # get band-specific camera settings
        for band in DeepspecBands.__args__:
            band_dict: dict = deepcopy(common_camera_settings)
            if 'camera' in spec_doc and band in spec_doc['camera']:
                    deep_dict_update(band_dict, spec_doc['camera'][band])

            # propagate 'exposure_duration' and 'number_of_exposures' to the camera settings
            band_dict['exposure_duration'] = common_camera_settings['exposure_duration']
            band_dict['number_of_exposures'] = common_camera_settings['number_of_exposures']

            new_spec_dict['spec']['camera'][band] = band_dict

    new_spec_dict['instrument'] = instrument

    # print("new_spec_dict:\n" + json.dumps(new_spec_dict, indent=2))
    try:
        spectrograph_model = SpectrographModel(**new_spec_dict)
    except ValidationError as e:
        print(f"====== ValidationError(s) =======\n")
        for err in e.errors():
            print(f"[ERR] {json.dumps(err, indent=2)}\n")
        raise
    return spectrograph_model


class EventModel(BaseModel):
    when: str   # datetime.isoformat
    what: str

class AssignedTaskModel(BaseModel, Activities):
    """
    A task ready for execution (already planned and scheduled)
    """
    model_config = ConfigDict(extra='allow')

    unit: Dict[str, TargetAssignmentModel]              # indexed by unit name, per-unit target assignment(s)
    task: AssignedTaskSettingsModel                     # general task stuff (ulid, etc.)
    events: Optional[List[EventModel]] = None           # things that happened to this task
    constraints: Optional[ConstraintsModel] = None

    @computed_field
    @property
    def unit_assignments(self) -> List[RemoteAssignment]:
        ret: List[RemoteAssignment] = []
        initiator = AssignmentInitiator.local_machine()
        for key in list(self.unit.keys()):
            unit_assignment: UnitAssignmentModel = UnitAssignmentModel(
                initiator=initiator,
                target=TargetAssignmentModel(ra=self.unit[key].ra, dec=self.unit[key].dec),
                task=self.task
            )

            units_specifier = parse_units(key)
            if units_specifier:
                units = RemoteAssignment.from_units_specifier(units_specifier, unit_assignment)
                if units:
                    ret += units
        return ret


    @computed_field
    @property
    def spec_assignment(self) -> RemoteAssignment | None:
        local_site = Config().local_site
        hostname = local_site.spec_host
        if hostname is None:
            return
        fqdn = f"{hostname}.{local_site.domain}"
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            ipaddr = None

        initiator = AssignmentInitiator.local_machine()
        try:
            spec_model = make_spec_model(self.model_extra['spec'])
            spec_assignment = SpectrographAssignmentModel(
                instrument=spec_model.instrument,
                initiator=initiator,
                task=self.task,
                spec=spec_model)
        except ValidationError as e:
            print("ValidationError(s)")
            for err in e.errors():
                print(f"ERR:\n  {err}")
            raise
        return RemoteAssignment(hostname=hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=spec_assignment)

    @classmethod
    def from_toml_file(cls,
                       toml_file: str,
                       activities = 0,
                       timings: List[Timing] = None,
                       commited_unit_apis = None,
                       unit_assignments = None,
                       spec_api = None,
                       spec_assignment = None):
        """
        Loads a TOML model from an assigned-task file.

        If the task doesn't have an ulid, allocates one and updates the file.

        :param toml_file: an assigned-task file in TOML format
        :param activities:
        :param timings:
        :param commited_unit_apis:
        :param unit_assignments:
        :param spec_assignment:
        :param spec_api:
        :return:
        """
        with open(toml_file, 'r') as fp:
            toml_doc = tomlkit.load(fp)

        just_created = False
        if 'ulid' not in toml_doc['task'] or not toml_doc['task']['ulid']:
            toml_doc['task']['ulid'] = str(ulid.new()).lower()
            just_created = True

        if 'file' not in toml_doc['task'] or not toml_doc['task']['file'] or toml_doc['task']['file'] != toml_file:
            toml_doc['task']['file'] = Path(os.path.realpath(toml_file)).as_posix()
            just_created = True

        if just_created:
            if not 'event' in toml_doc:
                toml_doc['events'] = {
                    'when': datetime.datetime.now().isoformat(),
                    'what': 'created'
                }
            with open(toml_file, 'w') as f:
                f.write(tomlkit.dumps(toml_doc))

        new_task = AssignedTaskModel(**toml_doc,
                       toml_file=toml_file,
                       activities=0,
                       timings={},
                       commited_unit_apis=[],
                       unit_assignments=[],
                       spec_assignment=None,
                       spec_api=None)

        return new_task

    def __repr__(self):
        return f"<AssignedTask>(ulid='{self.task.ulid}')"

    @staticmethod
    async def api_coroutine(api, method: str, sub_url: str, data=None, json: dict | None = None):
        """
        An asynchronous coroutine for remote APIs

        :param api:
        :param method:
        :param sub_url:
        :param data:
        :param json:
        :return:
        """

        response = None
        try:
            if method == 'GET':
                response = await api.get(sub_url)
            elif method == 'PUT':
                response = await api.put(sub_url, data=data, json=json)
        except Exception as e:
            logger.error(f"api_coroutine: error {e}")
            raise
        return response

    async def fetch_statuses(self, units: List[UnitApi], spec: SpecApi | None = None):
        tasks = [self.api_coroutine(api=unit, method='GET', sub_url='status') for unit in units]
        if spec:
            tasks.append(self.api_coroutine(api=spec, method='GET', sub_url='status'))
        status_responses = await asyncio.gather(*tasks, return_exceptions=True)

        if spec:
            return status_responses[:-1], status_responses[-1]
        else:
            return status_responses

    async def get_spec_status(self) -> dict | None:
        status_response = await self.spec_api.get(method='status')
        canonical_response = CanonicalResponse(**status_response)
        if not canonical_response.succeeded:
            canonical_response.log(_logger=logger, label='spec')
            await self.abort()
            self.end_activity(AssignmentActivities.ExposingSpec)
            return None

        return canonical_response.value

    def fail(self, reasons: List[str]):
        """
        Handles failure of an assigned task
        :param reasons:
        :return:
        """
        # add event to the task
        logger.error(f"failing task '{self.task.ulid}', reasons:")
        for reason in reasons:
            logger.error(f"  {reason}")

        path = Path(self.model_extra['toml_file'])
        new_path = Path(os.path.join(path.parent.parent, 'failed', path.name))
        os.makedirs(new_path.parent, exist_ok=True)
        shutil.move(str(path), str(new_path))
        logger.info(f"moved task '{self.task.ulid}' from {str(path)} to {str(new_path)}")

    async def execute(self):
        """
        Checks if the allocated components (units and spectrograph) are available and operational
        Dispatches assignments to units and waits for a quorum of them to reach 'guiding'
        Sends the assignment to the spectrograph
        Waits for spectrograph to finish the assignment
        Tells units to end 'guiding'
        :return:
        """
        self.spec_api = SpecApi(Config().local_site.name)
        unit_apis = []
        for remote_assignment in self.unit_assignments:
            unit_apis.append(UnitApi(ipaddr=remote_assignment.ipaddr, domain=ApiDomain.Unit))

        self.start_activity(AssignmentActivities.Executing)

        # Phase #1: check the required components are operational
        self.start_activity(AssignmentActivities.Probing)
        unit_responses, spec_response = await self.fetch_statuses(unit_apis, self.spec_api)

        # see what units respond at all
        detected_unit_apis = [unit_api for unit_api in unit_apis if unit_api.detected]
        n_detected = len(detected_unit_apis)
        if n_detected < self.task.quorum:
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            if n_detected == 0:
                self.fail(reasons=[f"no units quorum, no units were detected (required: {self.task.quorum})"])
            else:
                self.fail(reasons=[f"no units quorum, detected only {n_detected} " +
                           f"({[unit_api.hostname for unit_api in detected_unit_apis]}), required {self.task.quorum}"])
            return
        logger.info(f"detected units quorum achieved ({n_detected} units detected out of {self.task.quorum} required)")

        if not self.spec_api.detected:
            # spec does not respond
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            self.fail(reasons=[f"spec not detected"])
            return

        # enough units were detected (they answered to API calls), now check if they are operational
        operational_unit_apis = []
        for i, response in enumerate(unit_responses):
            unit_api = unit_apis[i]

            if isinstance(response, Exception):     # exception during HTTP fetch
                logger.error(f"unit '{unit_api.hostname}' ({unit_api.ipaddr}), {response=}")
            elif isinstance(response, dict) and 'operational' in response:
                if response['operational']:
                    operational_unit_apis.append(unit_apis[i])
                    logger.info(f"unit '{unit_api.hostname}' ({unit_api.ipaddr}), operational")
                else:
                    why_not_operational = response['why_not_operational']
                    logger.info(f"unit '{unit_api.hostname}' ({unit_api.ipaddr}), not operational: {why_not_operational}")

        if self.task.production and len(operational_unit_apis) < self.task.quorum:
            # not enough units are operational
            self.end_activity(AssignmentActivities.Probing)
            self.end_activity(AssignmentActivities.Executing)
            n_operational_units = len(operational_unit_apis)
            if n_operational_units == 0:
                self.fail(reasons=[f"no operational units (quorum: {self.task.quorum})"])
            else:
                self.fail(reasons=[f"only {n_operational_units} operational units (quorum: {self.task.quorum})"])
            return

        if isinstance(spec_response, Exception):
            logger.error(f"spec api exception: {self.spec_api.ipaddr}, {spec_response=}")
        elif spec_response and 'operational' in spec_response and not spec_response['operational']:
            self.fail(reasons=[f"spec is not operational {spec_response['why_not_operational']}"])
            return

        self.end_activity(AssignmentActivities.Probing)

        # Phase #2: we have a quorum of responding units and a responding spec, we can dispatch the assignments
        self.start_activity(AssignmentActivities.Dispatching)
        assignment_tasks = []
        for operational_unit_api in operational_unit_apis:
            for unit_assignment in self.unit_assignments:
                if operational_unit_api.ipaddr == unit_assignment.ipaddr:
                    assignment_tasks.append(
                        self.api_coroutine(operational_unit_api,
                                           method='PUT',
                                           sub_url='execute_assignment',
                                           json=unit_assignment.assignment.model_dump()))
                    break
        unit_responses = await asyncio.gather(*assignment_tasks, return_exceptions=True)
        self.end_activity(AssignmentActivities.Dispatching)

        for i, response in enumerate(unit_responses):
            try:
                canonical_response = CanonicalResponse(**response)
                if canonical_response.succeeded:
                    self.commited_unit_apis.append(operational_unit_apis[i])
                else:
                    canonical_response.log(_logger=logger, label=f"{operational_unit_apis[i].hostname} ({operational_unit_apis[i].ipaddr})")

            except Exception as e:
                logger.error(f"non-canonical response (error: {e}), ignoring!")
                continue

        n_committed = len(self.commited_unit_apis)
        if self.task.production and n_committed < self.task.quorum:
            if n_committed == 0:
                msg = f"no committed units (quorum: {self.task.quorum})"
            else:
                msg = f"only {n_committed} units (quorum: {self.task.quorum})"
            self.fail(reasons=[msg])
            self.end_activity(AssignmentActivities.Dispatching)
            self.end_activity(AssignmentActivities.Executing)
            return

        # the units are committed to their assignments, now wait for them to reach 'guiding'
        start = datetime.datetime.now()
        reached_guiding = False
        self.start_activity(AssignmentActivities.WaitingForGuiding)
        while (datetime.datetime.now() - start).seconds < self.task.timeout_to_guiding:
            time.sleep(20)
            statuses: List[Dict] = await self.fetch_statuses(self.commited_unit_apis)
            if all([(status['activities'] & UnitActivities.Guiding) for status in statuses]):
                logger.info(f"all commited units have reached 'Guiding'")
                reached_guiding = True
                break
        self.end_activity(AssignmentActivities.WaitingForGuiding)

        if not reached_guiding:
            self.fail(reasons=[f"did not reach 'guiding' within {self.task.timeout_to_guiding} seconds"])
            self.end_activity(AssignmentActivities.Executing)
            await self.abort()
            return

        self.start_activity(AssignmentActivities.ExposingSpec)

        # get (again) the spectrograph's status and make sure it is operational and not busy
        status = await self.get_spec_status()
        if self.task.production and not status['operational']:
            logger.error(f"spectrograph became non-operational, aborting!")
            self.end_activity(AssignmentActivities.Executing)
            await self.abort()
            return

        if status['activities'] != Activities.Idle:
            logger.error(f"spectrograph is busy (activities={status['activities']}), aborting!")
            self.end_activity(AssignmentActivities.Executing)
            await self.abort()
            return

        status_response = await self.spec_api.put(
            method='execute_assignment',
            json=self.spec_assignment.assignment.model_dump())

        canonical_response = CanonicalResponse(**status_response)
        if not canonical_response.succeeded:
            canonical_response.log(_logger=logger, label="spec rejected assignment")
            await self.abort()
            self.end_activity(AssignmentActivities.Executing)
            return

        self.start_activity(AssignmentActivities.WaitingForSpecDone)
        while True:
            time.sleep(60)
            spec_status = await self.get_spec_status()
            if not spec_status['operational']:
                for err in spec_status['why_not_operational']:
                    logger.error(f"spec not operational: {err}")
                await self.abort()
                self.end_activity(AssignmentActivities.WaitingForSpecDone)
                self.end_activity(AssignmentActivities.Executing)
                return

            if spec_status['activities'] == Activities.Idle:
                logger.info('spec is done')
                self.end_activity(AssignmentActivities.WaitingForSpecDone)
                self.end_activity(AssignmentActivities.Executing)
                break

    async def abort(self):
        self.start_activity(AssignmentActivities.Aborting)
        tasks = [self.api_coroutine(unit_api, method='GET', sub_url='abort') for unit_api in self.commited_unit_apis]
        if self.spec_api:
            tasks.append(self.api_coroutine(self.spec_api, method='GET', sub_url='abort'))
        self.end_activity(AssignmentActivities.Aborting)


class TaskNotification(BaseModel):
    """
    Sent to the controller by:
    - the units, as soon as they know the path of either an 'autofocus' or 'acquisition' folder
    - the spec, as soon as it has the path of the acquisition
    """
    unit: str
    ulid: str
    type: Literal['autofocus', 'acquisition', 'spec']
    path: str


async def main():
    # task_file = '/Storage/mast-share/MAST/tasks/assigned/TSK_assigned_highspec_task.toml'
    task_file = '/Storage/mast-share/MAST/tasks/assigned/TSK_assigned_deepspec_task.toml'
    try:
        assigned_task: AssignedTaskModel = AssignedTaskModel.from_toml_file(task_file)
    except ValidationError as e:
        # for err in e.errors():
        #     print('ERR: ' + err)
        raise

    remote_assignment = assigned_task.spec_assignment
    print(remote_assignment.model_dump_json(indent=2))

    spec_api = SpecApi()
    logger.info(f"sending task '{remote_assignment.assignment.task.ulid}' ({remote_assignment.assignment.spec.instrument}) to '{spec_api.hostname}' ({spec_api.ipaddr})")
    canonical_response = await spec_api.put(method='execute_assignment', json=remote_assignment.model_dump())
    if canonical_response.succeeded:
        logger.info(f"[{spec_api.ipaddr}] ACCEPTED task '{remote_assignment.assignment.task.ulid}'")
    else:
        logger.error(f"[{spec_api.ipaddr}] REJECTED task '{remote_assignment.assignment.task.ulid}'")
        for err in canonical_response.errors:
            logger.error(f"[{spec_api.ipaddr}] {err}")

if __name__ == '__main__':
    asyncio.run(main())
