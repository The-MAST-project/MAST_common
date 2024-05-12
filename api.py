import httpx
from common.utils import BASE_UNIT_PATH, BASE_SPEC_PATH, init_log
from enum import Enum, auto
import re
import logging
import asyncio

logger = logging.getLogger("api")
init_log(logger)


class ApiDomain(Enum):
    Unit = auto()
    Spec = auto()


api_ports = {
    ApiDomain.Unit: 8000,
    ApiDomain.Spec: 8000,
}

api_devices = {
    ApiDomain.Unit: ["mount", "focuser", "camera", "stage", "covers"],
    ApiDomain.Spec: []
}


class ApiClient:
    """
    Creates an API interface to a MAST entity living on a remote host.
    Currently supported entities are 'spec' and 'unit'
    Parameters:
     - hostname (str): The host name or address
     - device (str) | None: A specific device.  If not specified, either 'spec' or 'unit' will be accessed

    Examples:
        - spec = ApiClient(hostname='spec')
        - focuser01 = ApiClient(hostname='mast01', device='focuser')
        - unit17 = ApiClient(hostname='mast17')

    """

    TIMEOUT: float = 10

    def __init__(self, hostname: str, device: str | None = None, timeout: float = TIMEOUT):

        mast_pattern = re.compile(r"^mast(0[1-9]|1[0-9]|20)$")

        if mast_pattern.match(hostname):
            self.domain = ApiDomain.Unit
        elif hostname == "spec":
            self.domain = ApiDomain.Spec
        else:
            raise ValueError(f"bad {hostname=}.  Allowed hostnames are [mast01..mast20] or spec")

        domain_base = BASE_UNIT_PATH if self.domain == ApiDomain.Unit else BASE_SPEC_PATH

        self.base_url = f"http://{hostname}:{api_ports[self.domain]}{domain_base}"
        if device:
            if device in api_devices[self.domain]:
                self.base_url += f"/{device}"
            else:
                raise Exception(f"bad {device=} for domain {self.domain}, allowed: {api_devices[self.domain]}")

        self.logger = logging.getLogger(f"api-client")
        init_log(self.logger)

        self.detected = False
        self.operational = False
        self.timeout = timeout
        response: ApiResponse = asyncio.run(self.get('status'))
        if response:
            self.detected = response.detected
            self.operational = response.operational

    async def get(self, method: str, params: dict | None = None):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url=f"{self.base_url}/{method}", params=params, timeout=self.timeout)
            except:
                return None
        return self.common_get_put(response)

    async def put(self, method: str, params: dict | None = None):
        async with httpx.AsyncClient() as client:
            try:
                response = await client.put(url=f"{self.base_url}/{method}", params=params, timeout=self.timeout)
            except Exception as e:
                return {'error': f"{e}"}
        return self.common_get_put(response)

    def common_get_put(self, response):
        line: str

        try:
            response.raise_for_status()
            canonical_response = response.json()['response']
            if 'exception' in canonical_response:
                self.logger.error(f"Remote exception:")
                for line in canonical_response['exception']:
                    self.logger.error(f"  [E] {line.removesuffix('\n')}")
            elif 'errors' in canonical_response:
                for err in canonical_response['errors']:
                    self.logger.error(f"Remote error: {err}")
            else:
                return ApiResponse(canonical_response['value'])

        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            self.logger.error(f"Request error (url={e.request.url}): {e}")
        except Exception as e:
            self.logger.error(f"An error occurred: {e}")


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


class ApiUnit:

    def __init__(self, host: str):
        self.client = None

        try:
            self.client = ApiClient(host)
        except ValueError as e:
            logger.error(f"{e}")


class ApiSpec:

    def __init__(self):
        self.client = None

        try:
            self.client = ApiClient("spec")
        except ValueError as e:
            logger.error(f"{e}")


async def main():
    try:
        unit = ApiClient(hostname='mast01')
        response = await unit.get('status')
        if response:
            print(f"unit.status(): {response=}")
    except:
        pass

    try:
        focuser = ApiClient(hostname='mast01', device='focuser')
        response = await focuser.get('status')
        if response:
            print(f"focuser.status(): {response=}")
    except:
        pass

    try:
        bad = ApiClient(hostname='mast01', device='screwdriver')
    except Exception as ex:
        print(f"exception: {ex}")

if __name__ == '__main__':
    asyncio.run(main())
