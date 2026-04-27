import socket
from typing import Literal

from pydantic import BaseModel, computed_field, model_validator

from common.config import Config
from common.const import Const
from common.models.batches import Batch
from common.models.deepspec import DeepspecSettings
from common.models.highspec import HighspecSettings
from common.models.plans import Plan
from common.models.spectrographs import SpectrographModel
from common.notifications import NotificationInitiator
from common.parsers import parse_units
from common.spec import SpecInstruments


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


class UnitAssignment(BaseModel):
    initiator: Initiator
    plan: Plan

    @computed_field
    def autofocus(self) -> bool:
        return self.plan.autofocus if self.plan.autofocus else False


class DeepSpecAssignment(BaseModel):
    instrument: SpecInstruments = "deepspec"
    settings: DeepspecSettings | None


class HighSpecAssignment(BaseModel):
    instrument: SpecInstruments = "highspec"
    settings: HighspecSettings


class SpectrographAssignment(BaseModel):
    """
    The spectrograph-related part of a FullAssignment, containing:
    - The initiator machine (usually the control machine)
    - The task
    - A spectrograph part, either for deepspec or highspec (discriminated by the instrument field)
    """

    instrument: SpecInstruments
    initiator: Initiator
    batch: Batch | None = None
    plan: Plan | None = None
    spec: SpectrographModel


class Manifest(BaseModel):
    """
    This is what gets sent out via UnitApi or SpecApi
    """

    hostname: str
    fqdn: str
    ipaddr: str | None
    assignment: UnitAssignment | SpectrographAssignment | None = None

    @classmethod
    def from_site_colon_unit(cls, site_colon_unit: str, assignment):
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
    ) -> list["Manifest"]:
        if isinstance(units_specifier, str):
            units_specifier = [units_specifier]
        ret: list[Manifest] = []
        for site_colon_unit in parse_units(units_specifier):
            remote = Manifest.from_site_colon_unit(
                site_colon_unit, assignment=assignment
            )
            if remote:
                ret.append(remote)
        return ret


AssignmentState = Literal["in-progress", "completed", "failed", "aborted"]


class AssignmentNotification(BaseModel):
    """
    This is what gets sent out via the AssignmentNotificationApi
    """

    type: Literal["assignment_notification"] = "assignment_notification"
    assignment_id: str  # ulid assigned by scheduler
    state: AssignmentState
    initiator: NotificationInitiator | None = None
    errors: list[str] | None = None
    shared_top: str | None = None
    shared_subpath: str | None = None

    def model_post_init(self):
        if self.initiator is None:
            from common.notifications import initiator
            self.initiator = initiator
