from typing import List
import socket
from common.config import Config
from common.mast_logging import init_log
from common.networking import WEIZMANN_DOMAIN
from common.utils import function_name, RepeatTimer, caller_name
import httpx
import logging
import time
from threading import Lock

logger = logging.getLogger('power-switch')
init_log(logger)
logging.getLogger('httpx').setLevel(logging.WARN)


class Outlet:

    def __init__(self, power_switch, index: int, name: str, state: bool):
        self.power_switch = power_switch
        if index >= DliPowerSwitch.NUM_OUTLETS:
            raise Exception(f"{index=} not in range({DliPowerSwitch.NUM_OUTLETS})")
        self.index: int = index
        self.name: str = name
        self.state: bool = state

    def __repr__(self) -> str:
        return f"Outlet(index={self.index}]: name='{self.name}', state={self.state})"


class DliPowerSwitch:

    NUM_OUTLETS: int = 8

    def __init__(self, unit_name: str):
        self.unit_name = unit_name
        self.hostname = self.unit_name.replace('mast', 'mastps') + '.' + WEIZMANN_DOMAIN
        self.auth = httpx.DigestAuth('admin', '1234')
        self.headers = {
            'X-CSRF': 'x',
            'Accept': 'application/json',
        }
        self.ipaddr = socket.gethostbyname(self.hostname)

        unit_conf = Config().get_unit(self.unit_name)
        if 'power_switch' not in unit_conf:
            raise Exception(f"Missing 'power_switch' in unit configuration {unit_conf=}")
        self.conf = unit_conf['power_switch']

        self.timeout = 2
        self.base_url = f"http://{self.ipaddr}/"

        self.lock = Lock()
        self.last_fetch: float = 0
        self.max_age_seconds = 30  # seconds
        self.outlets: List[Outlet] = []
        self.fetch_outlets(force=True)
        self.upload_outlet_names()

        timer = RepeatTimer(interval=self.max_age_seconds, function=self.fetch_outlets)
        timer.name = 'dli-outlets-fetcher'
        # timer.start()

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

    def fetch_outlets(self, force=False):
        op = function_name()

        if not force and self.last_fetch != 0 and (time.time() - self.last_fetch) < self.max_age_seconds:
            return

        logger.info(f"fetching outlets {"(forced)" if force else ''} from {caller_name()}")
        names = self.get('restapi/relay/outlets/all;/name/')
        states = self.get('restapi/relay/outlets/all;/state/')

        if len(names) != DliPowerSwitch.NUM_OUTLETS:
            raise Exception(f"{op}: expected {DliPowerSwitch.NUM_OUTLETS}, got {len(names)}")
        if len(names) != len(states):
            raise Exception(f"{op}: got {len(names)} names but {len(states)} states!")

        with self.lock:
            self.outlets = []
            for i in range(0, len(names)):
                self.outlets.append(Outlet(self, index=i, name=names[i], state=states[i]))
        self.last_fetch = time.time()

    def get_outlet(self, identifier: str | int) -> Outlet | None:
        with self.lock:
            if isinstance(identifier, int):
                return self.outlets[identifier]

            result = [o for o in self.outlets if o.name == identifier]
            return result[0] if result else None

    def upload_outlet_names(self):
        """
        Uploads the outlet names, as configured
        """
        keys = self.conf['outlets'].keys()
        for _id, key in enumerate(keys):
            self.set_outlet_name(_id, self.conf['outlets'][key])
        self.fetch_outlets(force=True)

    def set_outlet_name(self, identifier: int | str, name: str):
        outlet: Outlet = self.get_outlet(identifier)
        self.put(f'restapi/relay/outlets/{outlet.index}/name/', data={'value': name})
        # self.fetch_outlets(force=True)

    def set_outlet_state(self, identifier: str | int, state: bool):
        outlet: Outlet = self.get_outlet(identifier)
        if outlet.state != state:
            self.put(url=f"restapi/relay/outlets/{outlet.index}/state/", data={'value': state})
            self.fetch_outlets(force=True)

    def toggle_outlet(self, identifier: str | int):
        outlet: Outlet = self.get_outlet(identifier)
        new_state = not outlet.state
        self.set_outlet_state(identifier=identifier, state=new_state)


class PowerSwitchFactory:
    _instances = {}

    @classmethod
    def get_instance(cls, unit_name: str) -> DliPowerSwitch:
        unit_conf = Config().get_unit(unit_name)
        conf = unit_conf['power_switch']

        if 'network' not in conf:
            raise Exception(f"missing 'network' in {conf=}")
        if 'ipaddr' not in conf['network']:
            raise Exception(f"missing 'ipaddr' in {conf['network']=}")
        ipaddr = conf['network']['ipaddr']

        if ipaddr not in cls._instances:
            cls._instances[ipaddr] = DliPowerSwitch(unit_name=unit_name)

        return cls._instances[ipaddr]

    def __init__(self):
        pass


class SwitchedOutlet:

    def __init__(self, unit_name: str | None = None, identifier: int | str | None = None):
        """
        A SwitchedOutlet consists of a PowerSwitch instance and an outlet number.
        """
        op = function_name()

        self.switch: DliPowerSwitch | None = None
        self.outlet_on_the_switch: Outlet | None = None

        if unit_name is None:
            unit_name = socket.gethostname()

        try:
            self.switch = PowerSwitchFactory.get_instance(unit_name=unit_name)
        except:
            raise Exception(f"{op}: could not get a DliPowerSwitch instance for {unit_name=}")

        # outlets have a numerical id (index), starting with 0 and a name
        if isinstance(identifier, int):  # 0 to 7
            if 0 > identifier >= len(self.switch.outlets):
                raise Exception(f"{op}: {identifier=} not in range({len(self.switch.outlets)}")
            self.outlet_on_the_switch = self.switch.outlets[identifier]

        elif isinstance(identifier, str):
            if identifier.isdigit():  # '1' to '8'
                n = int(identifier) - 1
                if 1 > n >= len(self.switch.outlets):
                    raise Exception(f"{op}: '{identifier=}' not in '1'..'8'")
                else:
                    self.outlet_on_the_switch = self.switch.outlets[n]
            else:  # an outlet name
                result = [_o for _o in self.switch.outlets if _o.name == identifier]
                if not result:
                    raise Exception(f"{op}: no outlet named '{identifier}' in {self.switch.outlets=}")
                else:
                    self.outlet_on_the_switch = result[0]

        self.delay_after_on = self.switch.conf['delay_after_on'] \
            if 'delay_after_on' in self.switch.conf else 0

    @property
    def id(self):
        return self.outlet_on_the_switch.index

    @property
    def name(self) -> str:
        return self.outlet_on_the_switch.name

    @property
    def state(self) -> bool:
        return self.outlet_on_the_switch.state

    def _power_on_off(self, state: bool):
        op = function_name()

        if self.outlet_on_the_switch.state != state:
            self.outlet_on_the_switch.state = state
            if state is True and self.delay_after_on:
                logger.info(f"{op}: delaying {self.delay_after_on} sec. after powering ON  ({self.name})")
                time.sleep(self.delay_after_on)

    def power_on(self):
        self._power_on_off(True)

    def power_off(self):
        self._power_on_off(False)

    def toggle(self):
        if self.is_on():
            self.power_off()
        else:
            self.power_on()

    def cycle(self):
        if self.is_on():
            self.power_off()
            time.sleep(3)
            self.power_on()
        else:
            self.power_on()

    def is_on(self) -> bool:
        return self.outlet_on_the_switch.state is True

    def is_off(self) -> bool:
        return self.outlet_on_the_switch.state is False

    def power_status(self):
        return {
            'powered': self.is_on(),
        }


if __name__ == '__main__':
    o8 = SwitchedOutlet('mastw', identifier='Outlet 8')
    print(f"[{o8.id}]: '{o8.name}', {o8.is_on()}")
    o8.toggle()
    print(f"[{o8.id}]: '{o8.name}', {o8.is_on()}")
