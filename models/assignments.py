import socket
from typing import Literal

import astropy.coordinates
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from common.config import Config
from common.const import Const
from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel
from common.models.spectrographs import SpectrographModel
from common.parsers import parse_units
from common.spec import Disperser


class TargetModel(BaseModel):
    ra: str | float = Field(description="RightAscension [sexagesimal or decimal]")
    dec: str | float = Field(description="Declination [sexagesimal or decimal]")

    @field_validator("ra")
    @classmethod
    def validate_ra(cls, value):
        """
        Validates RightAscension inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        ra = astropy.coordinates.Longitude(value, unit="hour").value
        return float(ra)  # converts np.float64 to float

    @field_validator("dec")
    @classmethod
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        dec = astropy.coordinates.Latitude(value, unit="deg").value
        return float(dec)  # converts np.float64 to float


class Initiator(BaseModel):
    """
    When the data is empty, populate with the local host
    """

    hostname: str | None
    fqdn: str | None
    ipaddr: str | None

    @model_validator(mode="before")
    @classmethod
    def validate_model(cls, values):
        hostname = values.get("hostname") or socket.gethostname()
        values["hostname"] = hostname

        values["fqdn"] = values.get("fqdn") or hostname + "." + Const.WEIZMANN_DOMAIN
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            try:
                ipaddr = socket.gethostbyname(values["fqdn"])
            except socket.gaierror:
                ipaddr = None
        values["ipaddr"] = ipaddr

        return values

    @classmethod
    def local_machine(cls):
        """
        The current machine as AssignmentInitiator
        :return:
        """
        hostname = socket.gethostname()
        fqdn = hostname + "." + Const.WEIZMANN_DOMAIN
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            try:
                ipaddr = socket.gethostbyname(fqdn)
            except socket.gaierror:
                ipaddr = None
        return cls(hostname=hostname, fqdn=fqdn, ipaddr=ipaddr)


class TaskSettingsModel(BaseModel):
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
    run_folder: str | None = None
    production: bool | None = Field(
        default=True, description="if 'false' some availability tests are more relaxed"
    )


class AssignmentModel(BaseModel):
    initiator: Initiator
    task: TaskSettingsModel


class UnitAssignmentModel(AssignmentModel):
    target: TargetModel

    @computed_field
    def autofocus(self) -> bool:
        return self.task.autofocus


class DeepSpecAssignment(BaseModel):
    instrument: Literal["deepspec"]
    settings: DeepspecModel | None


class HighSpecAssignment(BaseModel):
    instrument: Literal["highspec"]
    disperser: Disperser
    settings: HighspecModel


class SpectrographAssignmentModel(BaseModel):
    instrument: Literal["deepspec", "highspec"]
    initiator: Initiator
    task: TaskSettingsModel
    spec: SpectrographModel


class RemoteAssignment(BaseModel):
    """
    This is what gets sent out via UnitApi or SpecApi
    """

    hostname: str
    fqdn: str
    ipaddr: str | None
    assignment: UnitAssignmentModel | SpectrographAssignmentModel

    @classmethod
    def from_site_colon_unit(
        cls, site_colon_unit: str, assignment
    ) -> "RemoteAssignment":

        site_name, unit_id = site_colon_unit.split(":")
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
    def from_units_specifier(
        cls, units_specifier: str | list[str], assignment
    ) -> list["RemoteAssignment"]:
        if isinstance(units_specifier, str):
            units_specifier = [units_specifier]
        ret: list[RemoteAssignment] = []
        for site_colon_unit in parse_units(units_specifier):
            remote = RemoteAssignment.from_site_colon_unit(
                site_colon_unit, assignment=assignment
            )
            if remote:
                ret.append(remote)
        return ret
