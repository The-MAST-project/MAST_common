import urllib.parse
from typing import List
import socket
from common.config import Config
from common.mast_logging import init_log
from common.networking import WEIZMANN_DOMAIN
import httpx
import logging

logger = logging.getLogger('dli')
init_log(logger)
logging.getLogger('httpx').setLevel(logging.DEBUG)


class Outlet:

    def __init__(self, number: int, name: str, state: bool):
        self.number: int = number
        self.name = name
        self.state = state

    def __repr__(self) -> str:
        return f"<Outlet[{self.number}]: name='{self.name}', state={self.state}>"


class DliPowerSwitch:

    NUM_OUTLETS: int = 8

    def __init__(self, unit_name: str):
        self.unit_name = unit_name
        self.name = self.unit_name.replace('mast', 'mastps') + '.' + WEIZMANN_DOMAIN
        self.auth = httpx.DigestAuth('admin', '1234')
        self.headers = {
            'X-CSRF': 'x',
            'Accept': 'application/json',
        }
        self.ipaddr = socket.gethostbyname(self.name)
        self.outlets: List[Outlet] = []
        self.conf = Config().get_unit(self.unit_name)['power_switch']
        self.timeout = 2
        self.base_url = f"http://{self.ipaddr}/"

        self.upload_outlet_names()
        self.fetch_names_and_states()

    def get(self, url: str, params: dict | None = None) -> dict | object:
        url = self.base_url + url

        with httpx.Client(trust_env=False, auth=self.auth) as client:
            try:
                response = client.get(url=url, params=params, timeout=self.timeout)
            except httpx.TimeoutException:
                logger.error(f"timeout after {self.timeout} seconds, {url=}")
                return {'error': 'timeout'}
            except Exception as e:
                # logger.error(f"exception: {e}")
                return {'error': f"{e}"}
        return self.common_get_put(response)

    def put(self, url: str, data: dict | None = None) -> object:
        url = self.base_url + url

        with httpx.Client(trust_env=False, auth=self.auth) as client:
            try:
                # logger.info(f"url: {url}, data: {data}")
                response = client.put(url=url, headers=self.headers, data=data, timeout=self.timeout)
            except httpx.TimeoutException:
                logger.error(f"timeout after {self.timeout} seconds, {url=}")
                return {'error': 'timeout'}
            except Exception as e:
                logger.error(f"exception: {e}")
                return {'error': f"{e}"}
        return self.common_get_put(response)

    @staticmethod
    def common_get_put(response: httpx.Response) -> object:
        line: str

        try:
            response.raise_for_status()
            s = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}")
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error (url={e.request.url}): {e}")
            return None
        except Exception as e:
            # logger.error(f"httpx: Exception: {e}")
            return None

        return s

    def fetch_names_and_states(self):
        self.outlets = []
        names = self.get('restapi/relay/outlets/all;/name/')
        states = self.get('restapi/relay/outlets/all;/state/')
        for i in range(0, DliPowerSwitch.NUM_OUTLETS):
            self.outlets.append(Outlet(number=i, name=names[i], state=states[i]))

    def get_outlet(self, by: str | int) -> Outlet:
        self.fetch_names_and_states()
        if isinstance(by, int):
            return self.outlets[by]
        for i in range(0, DliPowerSwitch.NUM_OUTLETS):
            if self.outlets[i].name == by:
                return self.outlets[i]

        raise ValueError(f"could not locate an outlet {by=}")

    def upload_outlet_names(self):
        keys = self.conf['outlets'].keys()
        for _id, key in enumerate(keys):
            self.set_outlet_name(_id, self.conf['outlets'][key])

    def set_outlet_name(self, _id: int, name: str):
        self.put(f'restapi/relay/outlets/{_id}/name/', data={'value': name})

    def set_outlet_state(self, outlet: str | int, state: bool):
        outlet = self.get_outlet(outlet)
        if outlet.state != state:
            self.put(url=f"restapi/relay/outlets/{outlet.number}/state/", data={'value': state})

    def toggle_outlet(self, outlet: str | int):
        outlet = self.get_outlet(outlet)
        self.put(url=f"restapi/relay/outlets/{outlet.number}/state/", data={'value': not outlet.state})


if __name__ == '__main__':
    dli = DliPowerSwitch(unit_name='mastw')
    # print(dli.outlets)
    # # print(dli.get_outlet('Mount'))
    # print(dli.get_outlet(7))
    # dli.toggle_outlet(7)
    # print(dli.get_outlet(7))
    dli.upload_outlet_names()
    # dli.set_outlet_name(7, 'Test')
