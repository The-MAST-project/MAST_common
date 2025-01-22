import socket
import asyncio
import httpx
from common.utils import BASE_UNIT_PATH, BASE_SPEC_PATH, BASE_CONTROL_PATH, CanonicalResponse
from common.mast_logging import init_log
from common.config import Config, Site, WEIZMANN_DOMAIN
from enum import Enum, auto
import re
import logging
from typing import Optional, Dict

logger = logging.getLogger("api")
init_log(logger)


class ApiDomain(Enum):
    Unit = auto()
    Spec = auto()
    Control = auto()


api_ports = {
    ApiDomain.Unit: 8000,
    ApiDomain.Spec: 8000,
}

api_devices = {
    ApiDomain.Unit: ["mount", "focuser", "camera", "stage", "covers"],
    ApiDomain.Spec: []
}


class ApiResponse:
    """
    Converts a hierarchical dictionary (received as an API response) into a hierarchical object
    """
    def __init__(self, dictionary: dict):
        for key, value in dictionary.items():
            if isinstance(value, dict):
                value = ApiResponse(value)
            elif isinstance(value, list):
                # Convert each item in the list if it's a dictionary
                value = [ApiResponse(item) if isinstance(item, dict) else item for item in value]
            setattr(self, key, value)

    def __repr__(self):
        attrs = ', '.join(f"{key}={value!r}" for key, value in self.__dict__.items())
        return f"{self.__class__.__name__}({attrs})"


class ApiClient:
    """
    Creates an API interface to a MAST entity living on a remote host.

    Parameters:
     - hostname Optional[str]: host name
     - ipaddr Optional[str]:  IPv4 address
     - device Optional[str]: A specific device.  If not specified, either 'spec' or 'unit' will be accessed
     - domain: ApiDomain: selects a Unit, Spec or Controller

    Examples:
        - spec = ApiClient(hostname='spec')
        - focuser01 = ApiClient(hostname='mast01', device='focuser')
        - unit17 = ApiClient(hostname='mast17')

    """

    TIMEOUT: float = 30

    def __init__(self,
                 hostname: Optional[str] = None,
                 ipaddr: Optional[str] = None,
                 domain: Optional[ApiDomain] = None,
                 device: Optional[str] = None,
                 timeout: Optional[float] = TIMEOUT):

        if hostname is None and ipaddr is None:
            raise ValueError(f"both 'hostname' and 'ipaddr' are None")

        if ipaddr is not None and domain is None:
            raise ValueError(f"if 'ipaddr' is provided a 'domain' must be provided as well")

        domain_base = None

        if ipaddr is not None:
            self.domain = domain
            self.ipaddr = ipaddr
            domain_base = BASE_UNIT_PATH if domain == ApiDomain.Unit else BASE_SPEC_PATH if domain == ApiDomain.Spec else BASE_CONTROL_PATH
        else:
            if hostname.endswith('-spec'):
                self.domain = ApiDomain.Spec
                domain_base = BASE_SPEC_PATH
            elif hostname.endswith('-control'):
                self.domain = ApiDomain.Control
                domain_base = BASE_CONTROL_PATH
            else:
                mast_pattern = re.compile(r"^mast(0[1-9]|1[0-9]|20|w)$")
                if mast_pattern.match(hostname):
                    self.domain = ApiDomain.Unit
                    domain_base = BASE_UNIT_PATH

            try:
                self.ipaddr = socket.gethostbyname(hostname)
                self.hostname = hostname
            except socket.gaierror:
                try:
                    self.ipaddr = socket.gethostbyname(hostname + '.' + WEIZMANN_DOMAIN)
                    self.hostname = hostname
                except socket.gaierror:
                    raise ValueError(f"cannot get 'ipaddr' for {hostname=}")

        self.base_url = f"http://{self.ipaddr}:{api_ports[self.domain]}{domain_base}"
        if device:
            if device in api_devices[self.domain]:
                self.base_url += f"/{device}"
            else:
                raise Exception(f"bad {device=} for domain {self.domain}, allowed: {api_devices[self.domain]}")

        self.detected = False
        self.operational = False
        self.timeout = timeout

    async def get(self, method: str, params: Optional[Dict] = None):
        url = f"{self.base_url}/{method}"
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                response = await client.get(url=url, params=params, timeout=self.timeout)
            except httpx.TimeoutException:
                logger.error(f"timeout after {self.timeout} seconds, {url=}")
                raise
            except Exception as e:
                logger.error(f"exception: {e}")
                raise
        return self.common_get_put(response)

    async def put(self, method: str, params: Optional[Dict] = None):
        url = f"{self.base_url}/{method}"
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                response = await  client.put(url=f"{self.base_url}/{method}", params=params, timeout=self.timeout)
            except httpx.TimeoutException:
                logger.error(f"timeout after {self.timeout} seconds, {url=}")
                # return {'error': 'timeout'}
                raise
            except Exception as e:
                logger.error(f"exception: {e}")
                # return {'error': f"{e}"}
                raise
        return self.common_get_put(response)

    def common_get_put(self, response: httpx.Response):
        line: str
        value = None

        try:
            response.raise_for_status()
            response_dict = response.json()
            if 'canonical' in response_dict and response_dict['canonical']:
                canonical_response = CanonicalResponse(**response_dict)
                if hasattr(canonical_response, 'exception') and  canonical_response.exception is not None:
                    e = canonical_response.exception
                    logger.error(f"Remote Exception     type: {e.type}")
                    logger.error(f"Remote Exception  message: {e.message}")
                    for arg in e.args:
                        logger.error(f"Remote Exception      arg: {arg}")
                    for line in e.traceback.split('\n'):
                        logger.error(f"Remote Exception traceback: {line}")

                elif hasattr(canonical_response, 'errors') and canonical_response.errors is not None:
                    for err in canonical_response.errors:
                        logger.error(f"Remote error: {err}")

                elif hasattr(canonical_response, 'value') and canonical_response.value is not None:
                    value = canonical_response.value

                else:
                    raise Exception(f"got a canonical response but fields 'exception', 'errors' and 'value' are all None")
            else:
                # shouldn't happen - we received a non-canonical api response
                value = response_dict

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error (url={e.request.url}): {e}")
            raise
        except Exception as e:
            logger.error(f"An error occurred: {e}")
            raise

        self.detected = True
        self.operational = True
        return value


class UnitApi(ApiClient):

    def __init__(self,
                 hostname: Optional[str] = None,
                 ipaddr: Optional[str] = None,
                 domain: Optional[ApiDomain] = None,
                 device: Optional[str] = None):
        super().__init__(hostname=hostname, ipaddr=ipaddr, device=device, domain=domain)


class SpecApi(ApiClient):

    def __init__(self, site_name: Optional[str] = None):
        self.client = None

        if site_name:
            site = [s for s in Config().sites if s.name == site_name][0]
        else:
            site: Site = Config().local_site
        super().__init__(hostname=f"{site.project}-{site.name}-spec")

class ControlApi:

    def __init__(self, site_name: Optional[str] = None):
        self.client = None

        if site_name:
            site = [s for s in Config().sites if s.name == site_name][0]
        else:
            site: Site = Config().local_site
        try:
            self.client = ApiClient(f"{site.project}-{site.name}-control")
        except ValueError as e:
            logger.error(f"{e}")


def main():
    try:
        unit = ApiClient(hostname='mast01')
        response = unit.get('status')
        if response:
            print(f"unit.status(): {response=}")
    except:
        pass

    try:
        focuser = ApiClient(hostname='mast01', device='focuser')
        response = focuser.get('status')
        if response:
            print(f"focuser.status(): {response=}")
    except:
        pass

    try:
        bad = ApiClient(hostname='mast01', device='screwdriver')
    except Exception as ex:
        print(f"exception: {ex}")


if __name__ == '__main__':
    main()
