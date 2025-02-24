from common.config import Config, WEIZMANN_DOMAIN
from common.parsers import parse_units
from common.spec import Disperser
import socket
from typing import List, Optional, Any, Literal, Union
from pydantic import BaseModel, computed_field, field_validator, model_validator, Field
import astropy.coordinates
from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel
from common.models.spectrographs import SpectrographModel


class TargetAssignmentModel(BaseModel):
    ra: Union[str, float] = Field(description='RighAscension [sexagesimal or decimal]')
    dec: Union[str, float] = Field(description='Declination [sexagesimal or decimal]')

    @field_validator('ra')
    def validate_ra(cls, value):
        """
        Validates RightAscension inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        ra = astropy.coordinates.Longitude(value, unit='hour').value
        return float(ra)  # converts np.float64 to float

    @field_validator('dec')
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        dec = astropy.coordinates.Latitude(value, unit='deg').value
        return float(dec)  # converts np.float64 to float


class Initiator(BaseModel):
    """
    When the data is empty, populate with the local host
    """
    hostname: Optional[str]
    fqdn: Optional[str]
    ipaddr: Optional[str]

    @model_validator(mode='before')
    def validate_model(cls, values):
        hostname = values.get('hostname') or socket.gethostname()
        values['hostname'] = hostname

        values['fqdn'] = values.get('fqdn') or hostname + '.' + WEIZMANN_DOMAIN
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            try:
                ipaddr = socket.gethostbyname(values['fqdn'])
            except socket.gaierror:
                ipaddr = None
        values['ipaddr'] = ipaddr

        return values

    @classmethod
    def local_machine(cls):
        """
        The current machine as AssignmentInitiator
        :return:
        """
        hostname = socket.gethostname()
        fqdn = hostname + '.' + WEIZMANN_DOMAIN
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            try:
                ipaddr = socket.gethostbyname(fqdn)
            except socket.gaierror:
                ipaddr = None
        return cls(hostname=hostname, fqdn=fqdn, ipaddr=ipaddr)


class AssignedTaskSettingsModel(BaseModel):
    ulid: Optional[str] = Field(default=None, description='Unique ID')
    file: Optional[str] = None
    owner: Optional[str] = None
    merit: Optional[int] = 1
    quorum: Optional[int] = Field(default=1, description='Least number of units')
    timeout_to_guiding: Optional[int] = Field(default=600, description="How long [seconds] to wait for all units to achieve 'guiding'")
    autofocus: Optional[bool] = Field(default=False, description="Should the units start with 'autofocus'")
    run_folder: Optional[str] = None
    production: Optional[bool] = Field(default=True, description="if 'false' some availability tests are more relaxed")


class AssignmentModel(BaseModel):
    initiator: Initiator
    task: AssignedTaskSettingsModel

class UnitAssignmentModel(AssignmentModel):
    target: TargetAssignmentModel

    @computed_field
    def autofocus(self) -> bool:
        return self.task.autofocus


class DeepSpecAssignment(BaseModel):
    instrument: Literal['deepspec']
    settings: Optional[DeepspecModel]

class HighSpecAssignment(BaseModel):
    instrument: Literal['highspec']
    disperser: Disperser
    settings: HighspecModel


class SpectrographAssignmentModel(BaseModel):
    instrument: Literal['deepspec', 'highspec']
    initiator: Initiator
    task: AssignedTaskSettingsModel
    spec: SpectrographModel


class RemoteAssignment(BaseModel):
    """
    This is what gets sent out via UnitApi or SpecApi
    """
    hostname: str
    fqdn: str
    ipaddr: Optional[str]
    assignment: Union[UnitAssignmentModel, SpectrographAssignmentModel]

    @classmethod
    def from_site_colon_unit(cls,
                             site_colon_unit: str,
                             assignment) -> "RemoteAssignment":

        site_name, unit_id = site_colon_unit.split(':')
        sites = Config().sites
        site = [s for s in sites if site_name == s.name][0]

        if unit_id.isdigit():
            unit_id = f"{int(unit_id):02}"

        hostname = f"{site.project}{unit_id}"
        fqdn = f"{hostname}.{site.domain}"
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            ipaddr = None

        return cls(hostname=hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=assignment)

    @classmethod
    def from_units_specifier(cls,
                  units_specifier: str | List[str],
                  assignment) -> List["RemoteAssignment"]:
        if isinstance(units_specifier, str):
            units_specifier = [units_specifier]
        ret: List[RemoteAssignment] = []
        for site_colon_unit in parse_units(units_specifier):
            remote = RemoteAssignment.from_site_colon_unit(site_colon_unit, assignment=assignment)
            if remote:
                ret.append(remote)
        return ret

