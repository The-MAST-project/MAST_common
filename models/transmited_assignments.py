import socket
from typing import Any

from pydantic import BaseModel

from common.config import Config
from common.parsers import parse_units


class AssignmentEnvelope(BaseModel):
    """
    This is what gets sent out via UnitApi or SpecApi
    """

    # from common.models.assignments import (
    #     SpectrographAssignmentModel,
    #     UnitAssignmentModel,
    # )

    hostname: str
    fqdn: str
    ipaddr: str | None
    assignment: Any | None = None  #: UnitAssignmentModel | SpectrographAssignmentModel

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
    ) -> list["AssignmentEnvelope"]:
        if isinstance(units_specifier, str):
            units_specifier = [units_specifier]
        ret: list[AssignmentEnvelope] = []
        for site_colon_unit in parse_units(units_specifier):
            remote = AssignmentEnvelope.from_site_colon_unit(
                site_colon_unit, assignment=assignment
            )
            if remote:
                ret.append(remote)
        return ret
