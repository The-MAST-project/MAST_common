import socket
from typing import Literal

from pydantic import BaseModel, computed_field, model_validator

from common.const import Const
from common.models.deepspec import DeepspecModel
from common.models.highspec import HighspecModel
from common.models.plans import Plan
from common.models.spectrographs import SpectrographModel
from common.models.targets import Target
from common.spec import Disperser


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


class AssignmentModel(BaseModel):
    initiator: Initiator
    plan: Plan


class UnitAssignmentModel(AssignmentModel):
    target: Target

    @computed_field
    def autofocus(self) -> bool:
        return self.plan.autofocus if self.plan.autofocus else False


class DeepSpecAssignment(BaseModel):
    instrument: Literal["deepspec"]
    settings: DeepspecModel | None


class HighSpecAssignment(BaseModel):
    instrument: Literal["highspec"]
    disperser: Disperser
    settings: HighspecModel


class SpectrographAssignmentModel(BaseModel):
    """
    The spectrograph-related part of a FullAssignment, containing:
    - The initiator machine (usually the control machine)
    - The task
    - A spectrograph part, either for deepspec or highspec (discriminated by the instrument field)
    """

    instrument: Literal["deepspec", "highspec"]
    initiator: Initiator
    plan: Plan
    spec: SpectrographModel
