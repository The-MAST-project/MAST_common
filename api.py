import httpx
from common.utils import BASE_UNIT_PATH, BASE_SPEC_PATH, init_log
from enum import Enum, auto
import re
import json
import logging
import asyncio


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

    def __init__(self, hostname: str, device: str | None = None):

        mast_pattern = re.compile(r"^mast(0[1-9]|1[0-9]|20)$")

        if mast_pattern.match(hostname):
            self.domain = ApiDomain.Unit
        elif hostname == "spec":
            self.domain = ApiDomain.Spec
        else:
            raise Exception(f"Bad {hostname=}.  Allowed hostnames are [mast01..mast20] or spec")

        domain_base = BASE_UNIT_PATH if self.domain == ApiDomain.Unit else BASE_SPEC_PATH

        self.base_url = f"http://{hostname}:{api_ports[self.domain]}/{domain_base}"
        if device:
            if device in api_devices[self.domain]:
                self.base_url += f"/{device}"
            else:
                raise Exception(f"Bad {device=} for domain {self.domain}.  Allowed: {api_devices[self.domain]}")

        self.logger = logging.getLogger(f"api-client")
        init_log(self.logger)

    async def get(self, method: str, params: dict | None = None):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url=f"{self.base_url}/{method}", params=params)
                response.raise_for_status()
                canonical_response = response.json()
                if 'exception' in canonical_response:
                    self.logger.error(f"Remote exception: {canonical_response['exception']}")
                elif 'errors' in canonical_response:
                    for err in canonical_response['errors']:
                        self.logger.error(f"Remote error: {err}")
                else:
                    return json.loads(canonical_response['value'])

        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            self.logger.error(f"Request error (url={e.request.url}): {e}")
        except Exception as e:
            self.logger.error(f"An error occurred: {e}")

    async def put(self, method: str, params: dict | None = None):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.put(url=f"{self.base_url}/{method}", params=params)
                response.raise_for_status()
                canonical_response = response.json()
                if 'exception' in canonical_response:
                    self.logger.error(f"Remote exception: {canonical_response['exception']}")
                elif 'errors' in canonical_response:
                    for err in canonical_response['errors']:
                        self.logger.error(f"Remote error: {err}")
                else:
                    return json.loads(canonical_response['value'])

        except httpx.HTTPStatusError as e:
            self.logger.error(f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            self.logger.error(f"Request error (url={e.request.url}): {e}")
        except Exception as e:
            self.logger.error(f"An error occurred: {e}")


async def main():
    unit = ApiClient(hostname='mast01')
    response = await unit.get('status')
    if response:
        print(response)

    focuser = ApiClient(hostname='mast01', device='focuser')
    response = await focuser.get('status')
    if response:
        print(response)

    bad = ApiClient(hostname='mast01', device='screwdriver')

if __name__ == '__main__':
    asyncio.run(main())
