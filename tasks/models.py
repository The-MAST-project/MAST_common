import asyncio
import datetime
import os.path
import socket
import time
from enum import IntFlag
import astropy.coordinates
import tomlkit
import ulid
from pydantic import BaseModel, field_validator, model_validator, ValidationError, computed_field, ConfigDict
import logging

from enum import auto
from common.activities import Activities, AssignmentActivities
from common.config import Config
from common.mast_logging import init_log
from common.parsers import parse_units
from common.spec import SpecGrating, BinningLiteral
from typing import Literal, List, Optional, Union, Dict
from common.tasks.target import RemoteAssignment
from common.models.assignments import AssignmentInitiator, TargetAssignmentModel, AssignedTaskGlobalsModel, UnitAssignmentModel, SpecAssignment
from common.models.assignments import SpectrographAssignment
from common.models.constraints import ConstraintsModel
from common.api import UnitApi, SpecApi, ApiDomain
from pathlib import Path
from common.activities import UnitActivities, Timing

from astropy.coordinates import Longitude, Latitude
from astropy import units as u

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
    spectrograph: SpectrographAssignment
    constraints: ConstraintsModel


class TaskModel(BaseModel):
    ulid: str
    file: str
    autofocus: bool = False


class AssignedTaskModel(BaseModel, Activities):
    """
    A task ready for execution (already planned and scheduled)
    """
    model_config = ConfigDict(extra='allow')

    unit: Dict[str, TargetAssignmentModel]
    task: AssignedTaskGlobalsModel
    # task: TaskModel
    spec: SpectrographAssignment

    @computed_field
    @property
    def unit_assignments(self) -> List[RemoteAssignment]:
        ret: List[RemoteAssignment] = []
        for key in list(self.unit.keys()):
            units_specifier = parse_units(key)
            pass
            unit_assignment: UnitAssignmentModel = UnitAssignmentModel(
                initiator=AssignmentInitiator(),
                target=TargetAssignmentModel(ra=self.unit[key].ra, dec=self.unit[key].dec),
                task=self.task
            )
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
            return None
        fqdn = f"{hostname}.{local_site.domain}"
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            ipaddr = None

        return RemoteAssignment(hostname=hostname,
                                fqdn=fqdn,
                                ipaddr=ipaddr,
                                assignment={
                                               'task': {
                                                   'ulid': self.task.ulid,
                                                   'file': self.task.file}
                                           } |
                                           {
                                               'initiator': AssignmentInitiator()
                                           } |
                                           self.spec.__dict__)

    @classmethod
    def from_toml_file(cls, file:str, activities = 0, timings: List[Timing] = None, commited_unit_apis = None, spec_api = None):
        """
        Loads a TOML model from an assigned-task file.

        If the task doesn't have an ulid, allocates one and updates the file.

        :param file: an assigned-task file in TOML format
        :param activities:
        :param timings:
        :param commited_unit_apis:
        :param spec_api:
        :return:
        """
        with open(file, 'r') as fp:
            toml_doc = tomlkit.load(fp)

        file_needs_update = False
        if 'ulid' not in toml_doc['task'] or not toml_doc['task']['ulid']:
            toml_doc['task']['ulid'] = str(ulid.new()).lower()
            file_needs_update = True

        if 'file' not in toml_doc['task'] or not toml_doc['task']['file']:
            toml_doc['task']['file'] = file
            file_needs_update = True

        if file_needs_update:
            with open(file, 'w') as f:
                f.write(tomlkit.dumps(toml_doc))

        new_task = cls(**toml_doc,
                       activities=0,
                       timings={},
                       commited_unit_apis=[],
                       spec_api=None)
        new_task.task.file = Path(os.path.realpath(file)).as_posix()
        return new_task

    def __repr__(self):
        return f"<AssignedTask>(ulid='{self.task.ulid}')"

    @staticmethod
    async def api_coroutine(api, method: str, sub_url: str, data=None, json: str | None = None):
        """
        An asynchronous coroutine for remote APIs

        :param api:
        :param method:
        :param sub_url:
        :param data:
        :param json:
        :return:
        """

        DEFAULT_TIMEOUT_SEC = 30

        response = None
        try:
            # if method == 'GET':
            #     response = await asyncio.wait_for(api.get(sub_url), timeout=DEFAULT_TIMEOUT_SEC)
            # elif method == 'POST':
            #     response = await asyncio.wait_for(api.post(sub_url, data=data), timeout=DEFAULT_TIMEOUT_SEC)
            if method == 'GET':
                response = await api.get(sub_url)
            elif method == 'PUT':
                response = await api.put(sub_url, data=data, json=json)
        except Exception as e:
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

    async def execute_assigned_task(self):
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
        for remote in self.unit_assignments:
            unit_apis.append(UnitApi(ipaddr=remote.ipaddr, domain=ApiDomain.Unit))

        # Phase #1: check the required components are operational
        self.start_activity(AssignmentActivities.Probing)

        unit_responses, spec_response = await self.fetch_statuses(unit_apis, self.spec_api)
        operational_unit_apis = []
        spec_is_responding = False
        for i, response in enumerate(unit_responses):
            unit_api = unit_apis[i]
            if isinstance(response, Exception):     # exception during HTTP fetch
                logger.error(f"unit api exception: {unit_api.ipaddr}, {response=}")
            elif isinstance(response, dict) and 'operational' in response:
                # if response['operational']:
                #     operational_unit_apis.append(unit_apis[i])
                #     logger.info(f"unit api: {unit_api.ipaddr}, operational")
                # else:
                #     why_not_operational = response['why_not_operational']
                #     logger.info(f"unit api: {unit_api.ipaddr}, not operational: {why_not_operational}")
                operational_unit_apis.append(unit_apis[i])

        if isinstance(spec_response, Exception):
            logger.error(f"spec api exception: {self.spec_api.ipaddr}, {spec_response=}")
        else:
            spec_is_responding = True
            logger.info(f"spec api: {self.spec_api.ipaddr}, {spec_response=}")

        # if not spec_is_responding:
        #     logger.error(f"spec at '{self.spec_api.ipaddr}' not responding, aborting {self.__repr__()}!")
        #     self.end_activity(AssignmentActivities.Probing)
        #     return
        # if len(operational_unit_apis) == 0:
        #     logger.error(f"no units are operational, aborting {self.__repr__()}!")
        #     self.end_activity(AssignmentActivities.Probing)
        #     return
        # elif len(operational_unit_apis) < self.task.quorum:
        #     logger.error(f"only {len(operational_unit_apis)} out of required {self.task.quorum} are operational, aborting {self.__repr__()}!")
        #     self.end_activity(AssignmentActivities.Probing)
        #     return

        self.end_activity(AssignmentActivities.Probing)


        logger.info(f"sending assignments to units")
        #
        # We have a quorum of responding units and a responding spec, we can dispatch the assignments
        #
        assignment_tasks = []
        for operational_unit_api in operational_unit_apis:
            for unit_assignment in self.unit_assignments:
                if operational_unit_api.ipaddr == unit_assignment.ipaddr:
                    assignment_tasks.append(
                        self.api_coroutine(operational_unit_api,
                                           method='PUT',
                                           sub_url='execute_assignment',
                                           json=unit_assignment.assignment.model_dump_json()))
                    break
        responses = await asyncio.gather(*assignment_tasks, return_exceptions=True)

        for i, response in enumerate(responses):
            if isinstance(response, dict) and 'value' in response and response['value'] == 'ok':
                self.commited_unit_apis.append(operational_unit_apis[i])

        start = datetime.datetime.now()
        reached_guiding = False
        while (datetime.datetime.now() - start).seconds < self.task.timeout_to_guiding:
            time.sleep(20)
            statuses: List[Dict] = await self.fetch_statuses(self.commited_unit_apis)
            if all([(status['activities'] & UnitActivities.Guiding) for status in statuses]):
                logger.info(f"all commited units have reached 'Guiding")
                reached_guiding = True
                break

        if not reached_guiding:
            logger.error(f"did not reach 'guiding' within {self.task.timeout_to_guiding} seconds, aborting {self.__repr__()}!")
            await self.end_assignments()


    async def end_assignments(self):
        tasks = [self.api_coroutine(unit_api, 'GET', 'abort') for unit_api in self.commited_unit_apis]
        if self.spec_api:
            tasks.append(self.api_coroutine(self.spec_api, 'GET', 'abort'))
        status_responses = await asyncio.gather(*tasks, return_exceptions=True)

async def main():
    task_file = os.path.join(os.path.dirname(__file__), 'assigned_task.toml')
    try:
        assigned_task: AssignedTaskModel = AssignedTaskModel.from_toml_file(task_file)
    except ValidationError as e:
        print(e)
        raise

    # transfer_task = assigned_task.model_dump_json()
    # loaded_task = AssignedTaskModel.model_validate_json(transfer_task)
    # print(loaded_task.spec_assignment.model_dump_json(indent=2))

    await assigned_task.execute_assigned_task()

if __name__ == '__main__':
    asyncio.run(main())
