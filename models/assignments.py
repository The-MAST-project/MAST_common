import socket
from typing import Literal

from pydantic import BaseModel, computed_field, model_validator

from common.config import Config
from common.config.local import load_local_config
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

        values["fqdn"] = values.get("fqdn") or hostname + "." + load_local_config().domain
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
        fqdn = hostname + "." + load_local_config().domain
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
        site = next((s for s in sites if site_name == s.name), None)
        if site is None:
            raise ValueError(f"unknown site '{site_name}' in '{site_colon_unit}', known sites: {[s.name for s in sites]}")

        if unit_id.isdigit():
            unit_id = f"{int(unit_id):02}"

        hostname = f"{site.project}{unit_id}"
        fqdn = f"{hostname}.{load_local_config().domain}"
        try:
            ipaddr = socket.gethostbyname(hostname)
        except socket.gaierror:
            ipaddr = None

        return cls(hostname=hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=assignment)

    @classmethod
    def from_units_specifier(cls, units_specifier: str | list[str], assignment) -> list["Manifest"]:
        if isinstance(units_specifier, str):
            units_specifier = [units_specifier]
        ret: list[Manifest] = []
        for site_colon_unit in parse_units(units_specifier):
            remote = Manifest.from_site_colon_unit(site_colon_unit, assignment=assignment)
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

    # Where the products live, **relative to the producer's shared root** -- e.g.
    # "2026-08-11/highspec/acquisition-0001", never "D:/MAST/..." or "Z:/MAST/...".
    #
    # It has to be relative because the two ends do not share a spelling. A producer's
    # shared root is `Z:/MAST/<hostname>/`; the controller's is `/Storage/mast-share/MAST`,
    # with the host as the next component. Only the receiver can name the path in a form
    # its own filesystem resolves, and it does: MAST_control symlinks
    # `Filer().shared.root / initiator.hostname / shared_top` into the run folder.
    #
    # A producer computes it from the ram-side folder it wrote into:
    #     os.path.relpath(folder, Filer().ram.root)
    # which is exactly the path `Filer.move_ram_to_shared` moves that folder to, since the
    # move only swaps ram.root for shared.root.
    #
    # This was previously an absolute ram path (MAST_spec#39): it named a directory that is
    # reaped once the products move, in a drive-letter spelling meaningless on the Linux
    # controller. `os.symlink` accepts a dangling target, so it failed silently.
    shared_top: str | None = None

    # Which kind of products these are ("acquisition", "autofocus", "deepspec", ...). Names
    # the symlink the controller creates under the run folder; not part of the source path.
    shared_subpath: str | None = None

    def model_post_init(self):
        if self.initiator is None:
            from common.notifications import initiator

            self.initiator = initiator
