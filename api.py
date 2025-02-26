import socket
import httpx
from common.utils import BASE_UNIT_PATH, BASE_SPEC_PATH, BASE_CONTROL_PATH, CanonicalResponse, function_name
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

    TIMEOUT: float = 20

    def __init__(self,
                 hostname: Optional[str] = None,
                 ipaddr: Optional[str] = None,
                 port: Optional[int] = 8000,
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
            domain_base = BASE_UNIT_PATH if domain == ApiDomain.Unit else BASE_SPEC_PATH if domain == ApiDomain.Spec \
                else BASE_CONTROL_PATH
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

            self.hostname = hostname
            try:
                self.ipaddr = socket.gethostbyname(hostname)
            except socket.gaierror:
                try:
                    self.ipaddr = socket.gethostbyname(hostname + '.' + WEIZMANN_DOMAIN)
                except socket.gaierror:
                    raise ValueError(f"cannot get 'ipaddr' for {hostname=}")

        if self.ipaddr is not None and hostname is None:
            try:
                hostname, _, _ = socket.gethostbyaddr(self.ipaddr)
                self.hostname = hostname
            except socket.herror:
                self.hostname = None

        self.base_url = f"http://{self.ipaddr}:{port}{domain_base}"
        if device:
            if device in api_devices[self.domain]:
                self.base_url += f"/{device}"
            else:
                raise Exception(f"bad {device=} for domain {self.domain}, allowed: {api_devices[self.domain]}")

        self.detected = False
        self.timeout = timeout
        self.errors = []

    @property
    def operational(self) -> bool:
        return len(self.errors) == 0

    async def get(self, method: str, params: Optional[Dict] = None):
        url = f"{self.base_url}/{method}"
        op = f"{function_name()}, {url=}"
        self.errors = []
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                response = await client.get(url=url, params=params, timeout=self.timeout)

            except httpx.TimeoutException:
                self.errors.append(f"{op}: timeout after {self.timeout} seconds, {url=}")
                self.detected = False
                return CanonicalResponse(errors=self.errors)

            except Exception as e:
                self.errors.append(f"{op}: exception: {e}")
                self.detected = False
                return CanonicalResponse(errors=self.errors)

        return self.common_get_put(response)

    async def put(self, method: str, params: Optional[Dict] = None, data: Optional[Dict] = None,
                  json: Optional[Dict] = None):
        url = f"{self.base_url}/{method}"
        op = f"{function_name()}, {url=}"
        self.errors = []
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                response = await client.put(
                    url=f"{self.base_url}/{method}",
                    headers={'Content-Type': 'application/json'},
                    params=params,
                    data=data,
                    json=json,
                    timeout=self.timeout)
            except httpx.TimeoutException:
                self.append_error(f"{op}: timeout after {self.timeout} seconds, {url=}")
                self.detected = False
                return CanonicalResponse(errors=self.errors)

            except Exception as e:
                self.append_error(f"{op}: exception: {e}")
                self.detected = False
                return CanonicalResponse(errors=self.errors)

        return self.common_get_put(response)

    def append_error(self, err: str):
        self.errors.append(err)
        # logger.error(err)

    def common_get_put(self, response: httpx.Response):
        line: str
        value = None
        op = function_name()

        try:
            response.raise_for_status()
            response_dict = response.json()
            self.detected = True
            if 'api_version' in response_dict and response_dict['api_version'] == '1.0':
                canonical_response = CanonicalResponse(**response_dict)
                if hasattr(canonical_response, 'exception') and canonical_response.exception is not None:
                    e = canonical_response.exception
                    self.append_error(f"{op}: Remote Exception     type: {e.type}")
                    self.append_error(f"{op}: Remote Exception  message: {e.message}")
                    for arg in e.args:
                        self.append_error(f"{op}: Remote Exception      arg: {arg}")
                    for line in e.traceback.split('\n'):
                        self.append_error(f"{op}: Remote Exception traceback: {line}")
                        return CanonicalResponse(errors=self.errors)

                elif hasattr(canonical_response, 'errors') and canonical_response.errors is not None:
                    for err in canonical_response.errors:
                        self.append_error(err)
                    return CanonicalResponse(errors=self.errors)

                elif hasattr(canonical_response, 'value') and canonical_response.value is not None:
                    value = canonical_response.value

                else:
                    self.append_error(f"{op}: got a canonical response but fields " +
                                      f"'exception', 'errors' and 'value' are all None")
                    return CanonicalResponse(errors=self.errors)
            else:
                value = response_dict
                logger.error(f"{op}: received NON canonical response, returning it as 'value'")

        except httpx.HTTPStatusError as e:
            self.append_error(f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}")
            return CanonicalResponse(errors=self.errors)
        except httpx.RequestError as e:
            self.append_error(f"{op}: Request error (url={e.request.url}): {e}")
            return CanonicalResponse(errors=self.errors)
        except Exception as e:
            self.append_error(f"{op}: An error occurred: {e}")
            return CanonicalResponse(errors=self.errors)

        self.detected = True
        return CanonicalResponse(value=value)


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
        port = Config().get_service(service_name='spec')['port']
        super().__init__(hostname=f"{site.project}-{site.name}-spec", port=port)


class ControllerApi:

    def __init__(self, site_name: Optional[str] = None):
        self.client = None

        if site_name:
            site = [s for s in Config().sites if s.name == site_name][0]
        else:
            site: Site = Config().local_site
        port = Config().get_service(service_name='control')['port']
        try:
            self.client = ApiClient(f"{site.project}-{site.name}-control", port=port)
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
        ApiClient(hostname='mast01', device='screwdriver')
    except Exception as ex:
        print(f"exception: {ex}")


if __name__ == '__main__':
    main()
