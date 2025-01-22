from mimetypes import inited

from pydantic_core.core_schema import computed_field

from common.config import Config, Site, WEIZMANN_DOMAIN
from common.parsers import parse_units
import socket
from typing import List, Optional, Dict
from pydantic import BaseModel, computed_field


class AssignmentInitiator(BaseModel):

    @computed_field
    def hostname(self) -> str:
        return socket.gethostname()

    @computed_field
    def fqdn(self) -> str:
        return socket.gethostname() + '.' + WEIZMANN_DOMAIN

    @computed_field
    def ipaddr(self) -> str | None:
        try:
            return socket.gethostbyname(socket.gethostname())
        except socket.gaierror:
            return None



class RemoteAssignment(BaseModel):
    hostname: str
    fqdn: str
    ipaddr: Optional[str]
    assignment: Dict

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
            remote = RemoteAssignment.from_site_colon_unit(site_colon_unit, assignment)
            if remote:
                ret.append(RemoteAssignment.from_site_colon_unit(site_colon_unit, assignment))
        return ret
