import socket

from pydantic import BaseModel, computed_field, model_validator

from common.const import Const
from common.models.deepspec import DeepspecSettings
from common.models.highspec import HighspecSettings
from common.models.batches import Batch
from common.models.plans import Plan
from common.models.spectrographs import SpectrographModel
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
    batch: Batch
    spec: SpectrographModel
