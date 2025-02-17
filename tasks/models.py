import asyncio
import datetime
import os.path
import shutil
import socket
import time

import tomlkit
import ulid
from pydantic import BaseModel, field_validator, model_validator, ValidationError, computed_field, ConfigDict
import logging

from common.activities import Activities, AssignmentActivities
from common.config import Config
from common.mast_logging import init_log
from common.parsers import parse_units
from typing import Literal, List, Optional, Union, Dict
from common.tasks.target import RemoteAssignment
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

from astropy.coordinates import Longitude, Latitude
from astropy import units as u

from common.utils import CanonicalResponse, deep_dict_update

logger = logging.getLogger('tasks')
init_log(logger)


class SettingsModel(BaseModel):
    name: Union[str, None] = None
    ulid: Union[str, None] = None
    owner: Optional[str] = None
    merit: Union[int, None] = None
    state: Literal['new', 'in-progress', 'postponed', 'canceled', 'completed'] = 'new'
    timeout_to_guiding: Union[int, None] = None

    @field_validator('owner')
    def validate_owner(cls, user: str) -> str:
        valid_users = Config().get_users()
        if user not in valid_users:
            raise ValueError(f"Invalid user '{user}'")
        user = Config().get_user(user)
        if not 'canOwnTasks' in user['capabilities']:
            raise ValueError(f"User '{user['name']}' cannot own tasks")
        return user['name']

class SpecificationModel(BaseModel):
    ra: Union[float, str, None] = None
    dec: Union[float, str, None] = None
    requested_units: Union[str, List[str], None] = None
    allocated_units: Union[str, List[str], None] = None
    quorum: Optional[int] = 1
    exposure: Optional[float] = 5 * 60
    priority: Optional[Literal['lowest', 'low', 'normal', 'high', 'highest', 'too']] = 'normal'
    magnitude: Optional[float]
    magnitude_band: Optional[str]

    @model_validator(mode='after')
    def validate_target(cls, values):
        quorum = values.quorum
        units = values.requested_units
        if len(units) < quorum:
            raise ValueError(f"Expected {quorum} units in 'specifications' section, got only {len(units)=} ({units=})")
        return values

    @field_validator('requested_units', mode='before')
    def validate_input_units(cls, specifiers: Union[str, List[str]]) -> List[str]:
        success, value = parse_units(specifiers if isinstance(specifiers, list) else [specifiers])
        if success:
            return [value]
        else:
            raise ValueError(f"Invalid units specifier '{specifiers}', errors: {value}")

    @field_validator('ra', mode='before')
    def validate_ra(cls, value):
        return Longitude(value, unit=u.hourangle).value if value is not None else None

    @field_validator('dec', mode='before')
    def validate_dec(cls, value):
        return Latitude(value, unit=u.deg).value if value is not None else None

class TargetModel(BaseModel):
    settings: SettingsModel
    specification: SpecificationModel
    constraints: ConstraintsModel


def make_spec_model(doc) -> SpectrographModel | None:
    if not 'instrument' in doc:
        logger.error(f"missing 'instrument' in {doc=}")
        return None
    instrument = doc['instrument']
    if instrument not in ['highspec', 'deepspec']:
        logger.error(f"bad '{instrument=}', must be either 'deepspec' or 'highspec")
        return None

    defaults = Config().get_specs()
    if instrument == 'highspec':
        new_dict = {
            'spec': {
                'exposure': doc['exposure'] if 'exposure' in doc
                    else defaults['highspec']['exposure'],
                'number_of_exposures': doc['camera']['number_of_exposures'] if 'number_of_exposures' in doc['camera']
                    else defaults['highspec']['settings']['number_of_exposures'],
                'camera': {}
            }
        }
        camera_settings: dict = defaults['highspec']['settings']
        if 'camera' in doc:
            deep_dict_update(camera_settings, doc['camera'])
        new_dict['spec']['camera'] = camera_settings

    else:
        new_dict = {
            'spec': {
                'exposure': doc['exposure'] if 'exposure' in doc
                else defaults['deepspec']['exposure'],
                'number_of_exposures': doc['number_of_exposures'] if 'number_of_exposures' in doc
                else defaults['deepspec']['common']['settings']['number_of_exposures'],
                'camera': {}
            }
        }
        common_camera_settings = deepcopy(defaults['deepspec']['common']['settings'])

        # get settings common to all cameras from doc
        for k, v in doc['camera'].items():
            if k in common_camera_settings:
                common_camera_settings[k] = doc['camera'][k]

        # get band-specific camera settings
        for band in DeepspecBands.__args__:
            band_dict: dict = deepcopy(common_camera_settings)
            if 'camera' in doc and band in doc['camera']:
                    deep_dict_update(band_dict, doc['camera'][band])
            new_dict['spec']['camera'][band] = band_dict

    new_dict['calibration'] = doc['calibration'] if 'calibration' in doc else {'lamp_on': False, 'filter': None}
    new_dict['instrument'] = instrument
    new_dict['spec']['instrument'] = instrument

    # print(json.dumps(new_dict, indent=2))
    return SpectrographModel(**new_dict)


class EventModel(BaseModel):
    when: str   # datetime.isoformat
    what: str

class AssignedTaskModel(BaseModel, Activities):
    """
    A task ready for execution (already planned and scheduled)
    """
    model_config = ConfigDict(extra='allow')

    unit: Dict[str, TargetAssignmentModel]
    task: AssignedTaskSettingsModel
    events: Optional[List[EventModel]] = None
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
        # return self.spec_assignment
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
        spec_model = make_spec_model(self.model_extra['spec'])
        assignment = SpectrographAssignmentModel(
            instrument=spec_model.instrument,
            initiator=initiator,
            task=self.task,
            spec=spec_model)
        return RemoteAssignment(hostname=hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=assignment)

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
        :param run_folder: tells units and spec to what run do their products belong
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
                toml_doc['event'] = {
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
                       spec_assignment=None)

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
            # no units responded
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

        if len(operational_unit_apis) < self.task.quorum:
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
        if n_committed < self.task.quorum:
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
        if not status['operational']:
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


class TaskProduct(BaseModel):
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
    task_file = os.path.join(os.path.dirname(__file__), 'assigned_highspec_task.toml')
    try:
        assigned_task: AssignedTaskModel = AssignedTaskModel.from_toml_file(task_file)
    except ValidationError as e:
        print(e)
        return

    # print(assigned_task.model_dump_json(indent=2))
    # print(json.dumps(assigned_task, indent=2))
    # transfer_task = assigned_task.model_dump_json()
    # loaded_task = AssignedTaskModel.model_validate_json(transfer_task)
    # print(loaded_task.spec_assignment.model_dump_json(indent=2))
    for unit_assignment in assigned_task.unit_assignments:
        print(f"------------ unit_assignment hostname={unit_assignment.hostname}, ipaddr: {unit_assignment.ipaddr} ----------")
        print(unit_assignment.model_dump_json(indent=2))

    spec_assignment = assigned_task.spec_assignment
    print(f"----------- spec_assignment hostname={spec_assignment.hostname}, ipaddr: {spec_assignment.ipaddr} -----------")
    print(assigned_task.spec_assignment.model_dump_json(indent=2))

    # await assigned_task.execute()

if __name__ == '__main__':
    asyncio.run(main())
