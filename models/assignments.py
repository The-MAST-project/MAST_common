from common.config import Config, Site, WEIZMANN_DOMAIN
from common.parsers import parse_units
from common.spec import SpecGrating, BinningLiteral
import socket
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, computed_field, field_validator, model_validator
import astropy.coordinates


class TargetAssignmentModel(BaseModel):
    ra: str | float
    dec: str | float

    @field_validator('ra')
    def validate_ra(cls, value):
        """
        Validates RightAscension inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        ra = astropy.coordinates.Longitude(value, unit='hour').value
        # NOTE: convert np.float64 to float
        return float(ra)

    @field_validator('dec')
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        dec = astropy.coordinates.Latitude(value, unit='deg').value
        # NOTE: convert np.float64 to float
        return float(dec)


class AssignmentInitiator(BaseModel):
    """
    When the data is empty, populate with the local host
    """
    hostname: str
    fqdn: str
    ipaddr: str

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

    @field_validator('hostname')
    def validate_hostname(cls, value) -> str:
        return value if value else socket.gethostname()

    @field_validator('fqdn')
    def validate_fqdn(cls, value) -> str:
        return value if value else socket.gethostname() + '.' + WEIZMANN_DOMAIN

    @field_validator('ipaddr')
    def validate_ipaddr(self) -> str | None:
        try:
            return socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            return None


class RemoteAssignment(BaseModel):
    hostname: str
    fqdn: str
    ipaddr: Optional[str]
    assignment: Any

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
                  assignment) -> list["RemoteAssignment"]:
        if isinstance(units_specifier, str):
            units_specifier = [units_specifier]
        ret: List[RemoteAssignment] = []
        for site_colon_unit in parse_units(units_specifier):
            remote = RemoteAssignment.from_site_colon_unit(site_colon_unit, assignment=assignment)
            if remote:
                ret.append(remote)
        return ret


class AssignedTaskGlobalsModel(BaseModel):
    ulid: Optional[str] = None
    file: Optional[str] = None
    owner: Optional[str] = None
    merit: Optional[int] = 1
    quorum: Optional[int] = 1
    timeout_to_guiding: Optional[int] = 600
    autofocus: Optional[bool] = False


class AssignmentModel(BaseModel):
    initiator: AssignmentInitiator
    task: AssignedTaskGlobalsModel


class UnitAssignmentModel(AssignmentModel):
    target: TargetAssignmentModel

    @computed_field
    def autofocus(self) -> bool:
        return self.task.autofocus


class CalibrationLampModel(BaseModel):
    on: bool
    filter: str

    @field_validator('filter')
    def validate_filter(cls, filter_name: str) -> str | None:
        thar_filters = Config().get_specs()['wheels']['ThAr']['filters']

        if filter_name not in thar_filters.values():
            raise ValueError \
                (f"Invalid filter '{filter_name}', currently mounted ThAr filters are: {[f"{k}:{v}" for k, v in thar_filters.items() if v]}")
        return filter_name


class SpectrographAssignment(BaseModel):
    exposure: float
    lamp: Optional[CalibrationLampModel]
    instrument: Literal['deepspec', 'highspec']
    x_binning: BinningLiteral = 1
    y_binning: BinningLiteral = 1

    @model_validator(mode='after')
    def validate_model(cls, model):
        if model.lamp:
            if not model.lamp.on:
                raise ValueError("If the lamp is specified, it must be either on or off")
            if not model.lamp.filter:
                raise ValueError("If the lamp is specified, it must have a filter")
        return model


class DeepSpecAssignment(SpectrographAssignment):
    pass


class HighSpecAssignment(SpectrographAssignment):
    disperser: SpecGrating


class SpecAssignment(AssignmentModel):
    job: DeepSpecAssignment | HighSpecAssignment
