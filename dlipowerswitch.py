from typing import List, Optional
import socket

from common.config import Config
from common.mast_logging import init_log
from common.networking import WEIZMANN_DOMAIN
from common.utils import function_name, canonic_unit_name
import httpx
import logging
import time
from threading import Lock
from enum import IntFlag, auto

logger = logging.getLogger('power-switch')
init_log(logger)
logging.getLogger('httpx').setLevel(logging.WARN)


class Outlet:

    def __init__(self, switch, label: str, state: bool):
        self.switch = switch
        self.label: str = label
        self.state: bool = state

    def __repr__(self) -> str:
        return f"Outlet('{self.label}': {'ON' if self.state else 'OFF'})"


class DliPowerSwitch:

    NUM_OUTLETS: int = 8

    def __init__(self, hostname: str, ipaddr: str | None, conf: dict):
        self.hostname = hostname
        self.ipaddr = ipaddr
        self.conf = conf
        self.fqdn = self.hostname + '.' + WEIZMANN_DOMAIN
        self._detected = False
        self.auth = httpx.DigestAuth('admin', '1234')
        self.headers = {
            'X-CSRF': 'x',
            'Accept': 'application/json',
        }
        if not ipaddr:
            try:
                self.ipaddr = socket.gethostbyname(self.hostname)
            except socket.gaierror:
                raise

        # unit_conf = Config().get_unit(self.unit_name)
        # if 'power_switch' not in unit_conf:
        #     raise Exception(f"Missing 'power_switch' in unit configuration {unit_conf=}")
        # self.conf = unit_conf['power_switch']

        self.timeout = 2
        self.base_url = f"http://{self.ipaddr}/"

        self.lock = Lock()
        self.max_age_seconds = 30  # seconds
        self.outlets: List[Outlet] = []
        self.upload_outlet_names(list(conf['outlets'].values()))
        self.fetch_outlets()

    @property
    def detected(self) -> bool:
        """
        Updated by every GET/PUT to the switch
        :return:
        """
        return self._detected

    def get(self, url: str, params: dict | None = None) -> dict | object:
        url = self.base_url + url

        with httpx.Client(trust_env=False, auth=self.auth) as client:
            try:
                # logger.info(f"GET {url=}")
                response = client.get(url=url, params=params, timeout=self.timeout)
                self._detected = True
            except httpx.TimeoutException:
                logger.error(f"timeout after {self.timeout} seconds, {url=}")
                self._detected = False
                return {'error': 'timeout'}
            except Exception as e:
                # logger.error(f"exception: {e}")
                self._detected = False
                return {'error': f"{e}"}
        return self.common_get_put(response)

    def put(self, url: str, data: dict | None = None) -> object:
        url = self.base_url + url

        with httpx.Client(trust_env=False, auth=self.auth) as client:
            try:
                # logger.info(f"PUT {url=}, {data=}")
                response = client.put(url=url, headers=self.headers, data=data, timeout=self.timeout)
                self._detected = True
            except httpx.TimeoutException:
                logger.error(f"timeout after {self.timeout} seconds, {url=}")
                self._detected = False
                return {'error': 'timeout'}
            except Exception as e:
                logger.error(f"exception: {e}")
                self._detected = False
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

    def fetch_outlets(self):
        """
        Fetches all the outlets names and states from the switch.
        :return:
        """
        op = function_name()

        names = self.get('restapi/relay/outlets/all;/name/')
        states = self.get('restapi/relay/outlets/all;/state/')

        if len(names) != DliPowerSwitch.NUM_OUTLETS:
            raise Exception(f"{op}: expected {DliPowerSwitch.NUM_OUTLETS}, got {len(names)}")
        if len(names) != len(states):
            raise Exception(f"{op}: got {len(names)} names but {len(states)} states!")

        with self.lock:
            self.outlets = []
            for i in range(0, len(names)):
                self.outlets.append(Outlet(self, label=names[i], state=states[i]))

    def fetch_outlet(self, identifier: int | str) -> Outlet:
        self.fetch_outlets()
        if isinstance(identifier, int):
            return self.outlets[identifier]
        identifier = [i for i in range(len(self.outlets)) if self.outlets[i].label == identifier][0]
        return self.outlets[identifier]

    def upload_outlet_names(self, names: List[str]):
        """
        Uploads the outlet names, as configured
        """
        for idx in range(len(names)):
            self.set_outlet_name(idx, names[idx])

    def set_outlet_name(self, idx: int, name: str):
        self.put(f'restapi/relay/outlets/{idx}/name/', data={'value': name})

    def set_outlet_state(self, idx: int, state: bool):
        outlet: Outlet = self.fetch_outlet(idx)
        if outlet.state != state:
            self.put(url=f"restapi/relay/outlets/{idx}/state/", data={'value': state})

    def toggle_outlet(self, idx: int):
        outlet: Outlet = self.fetch_outlet(idx)
        new_state = not outlet.state
        self.set_outlet_state(idx=idx, state=new_state)


class PowerSwitchFactory:
    _instances = {}

    @classmethod
    def get_instance(cls, name: Optional[str] = None) -> DliPowerSwitch:
        """
        This needs to be generic enough as to fit all the MAST power switches.
        It basically gets the power switch's 'ipaddr' either via get-addr-info or via the MAST
          database, according to the provided 'name'
         - name == None: Get the power switch for the current unit
         - name == <unit-name> Get the power switch for the named unit
         - name == 'mast-spec-ps'<number>: One of the spectrograph's power switches

        The 'ipaddr' is searched, in this order:
         - via socket.gethostbyname(), without then with domain name
         - in the MAST configuration db.

        Raises: ValueError if the 'ipaddr' cannot be found
        """
        op = function_name()

        conf = None
        ps_name = None
        if name is None:
            unit_name = socket.gethostname()
            ps_name = unit_name.replace('mast', 'mastps')
            conf = Config().get_unit(unit_name)['power_switch']
        else:
            unit_name = canonic_unit_name(name)
            if unit_name is not None:
                ps_name = unit_name.replace('mast', 'mastps')
                conf = Config().get_unit(unit_name)['power_switch']
            elif name.startswith('mast-spec-ps') and name[len('mast-spec-ps'):].isdigit():
                ps_name = name
                conf = Config().get_specs['power_switch'][name]

        if not ps_name:
            raise ValueError(f"{op}: Bad name '{name}'")

        ipaddr = None
        try:
            # try to GAI solve the name
            ipaddr = socket.gethostbyname(ps_name)
        except socket.gaierror:
            try:
                # try to GAI solve the fully qualified name
                ipaddr = socket.gethostbyname(ps_name + '.' + WEIZMANN_DOMAIN)
            except socket.gaierror:
                pass

        if ipaddr is None:
            # We could not GAI resolve the name, maybe it's in the configuration database
            conf = Config().get_specs()['power_switch']
            if ps_name in conf and 'ipaddr' in conf[ps_name]:
                ipaddr = conf[ps_name]['ipaddr']
                conf = conf[ps_name]

        if ipaddr is None:
            raise ValueError(f"cannot get 'ipaddr' for '{ps_name}")

        # We have an 'ipaddr'
        if ipaddr not in cls._instances:
            # we don't have an instance for this 'ipaddr', make a new one
            cls._instances[ipaddr] = DliPowerSwitch(hostname=ps_name, ipaddr=ipaddr, conf=conf)

        return cls._instances[ipaddr]

    def __init__(self):
        pass


class OutletDomain(IntFlag):
    Unit = auto()
    Spec = auto()
    Unnamed = auto()



class SwitchedOutlet:

    valid_names = {
        OutletDomain.Unit: ['Mount', 'Stage', 'Camera', 'Focuser', 'Covers', 'Computer'],
        OutletDomain.Spec: [
            'ThArWheel', 'ThArLamp', 'qThWheel', 'qThLamp',
            'DeepspecU', 'DeepspecG', 'DeepspecR', 'DeepspecI',
            'Highspec', 'Chiller', 'Stage',
        ],
        OutletDomain.Unnamed: [
            'Outlet1', 'Outlet2', 'Outlet3', 'Outlet4',
            'Outlet5', 'Outlet6', 'Outlet7', 'Outlet8',
        ],
    }

    def __init__(self, domain: OutletDomain, outlet_name: str, unit_name: Optional[str] = None):
        """
        SwitchedOutlets belong to an OutletDomain and have a canonical name,
          valid within that domain.
        """
        op = function_name()

        self.switch: DliPowerSwitch | None = None
        self.outlet_name = outlet_name

        if (self.outlet_name not in SwitchedOutlet.valid_names[domain] and self.outlet_name
                not in SwitchedOutlet.valid_names[OutletDomain.Unnamed]):
            raise ValueError(f"{op}: bad outlet name '{self.outlet_name}' for {domain=}, " +
                             f"not in {SwitchedOutlet.valid_names[domain]} or " +
                             f"{SwitchedOutlet.valid_names[OutletDomain.Unnamed]}")

        if domain == OutletDomain.Unit:
            # Unit outlets have always the same names but the socket number may differ from unit to unit
            if unit_name is None:
                unit_name = socket.gethostname()
            try:
                self.switch = PowerSwitchFactory.get_instance(name=unit_name)
            except ValueError:
                raise
            try:
                conf = Config().get_unit(unit_name=unit_name)['power_switch']
                if self.outlet_name not in conf['outlets'].values():
                    raise ValueError(f"outlet name '{self.outlet_name}' not in {list(conf['outlets'].values())}")
            except:
                raise

        elif domain == OutletDomain.Spec:
            # Spec outlets have pre-defined names but may belong to any one of the spec power switches
            conf = Config().get_specs()['power_switch']
            for key in conf.keys():
                if self.outlet_name in conf[key]['outlets']:
                    # we located the switch
                    try:
                        self.switch = PowerSwitchFactory.get_instance(name=key)
                        outlet_names = [o.label for o in self.switch.outlets]
                        if self.outlet_name not in outlet_names:
                            raise ValueError(f"outlet name '{self.outlet_name} not in {outlet_names}")
                    except:
                        raise

        self.delay_after_on = self.switch.conf['delay_after_on'] if 'delay_after_on' in self.switch.conf else 0

    def __repr__(self):
        # logger.info(f"__repr__: {self.outlet_name=}")
        outlet = self.switch.fetch_outlet(self.outlet_name)
        return outlet.__repr__()

    @property
    def name(self) -> str:
        return self.outlet_name

    @property
    def state(self) -> bool:
        self.switch.fetch_outlets()
        return [o.state for o in self.switch.outlets if o.label == self.outlet_name][0]

    def power_on_or_off(self, new_state: bool):
        op = function_name()

        self.switch.fetch_outlets()
        outlet = [o for o in self.switch.outlets if o.label == self.outlet_name][0]
        if outlet.state != new_state:
            outlet.state = new_state
            if new_state is True and self.delay_after_on:
                logger.info(f"{op}: delaying {self.delay_after_on} sec. after powering ON  ({self.name})")
                time.sleep(self.delay_after_on)

    def power_on(self):
        self.power_on_or_off(True)

    def power_off(self):
        self.power_on_or_off(False)

    def toggle(self):
        idx = [i for i in range(len(self.switch.outlets)) if self.outlet_name == self.switch.outlets[i].label][0]
        self.switch.toggle_outlet(idx)

    def cycle(self):
        if self.is_on():
            self.power_off()
            time.sleep(3)
            self.power_on()
        else:
            self.power_on()

    def is_on(self) -> bool:
        self.switch.fetch_outlets()
        outlet = [o for o in self.switch.outlets if o.label == self.outlet_name][0]
        return outlet.state is True

    def is_off(self) -> bool:
        self.switch.fetch_outlets()
        outlet = [o for o in self.switch.outlets if o.label == self.outlet_name][0]
        return outlet.state is False

    def power_status(self):
        return {
            'powered': self.is_on(),
        }


if __name__ == '__main__':
    o8 = SwitchedOutlet(domain=OutletDomain.Unit, unit_name='mastw', outlet_name='Outlet8')
    print(f"Original: {o8}")
    o8.toggle()
    print(f"After toggle: {o8}")
