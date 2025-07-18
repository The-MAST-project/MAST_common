import contextlib
import logging
import socket
import time
from enum import IntFlag, auto
from json import JSONDecodeError
from threading import Lock

import httpx
from pydantic import BaseModel

from common.config import Config, PowerSwitchConfig
from common.const import Const
from common.interfaces.components import Component
from common.mast_logging import init_log

TriStateBool = bool | None

logger = logging.getLogger("power-switch")
init_log(logger)
logging.getLogger("httpcore").setLevel(logging.WARN)
logging.getLogger("httpx").setLevel(logging.WARN)


class PowerSwitchStatus(BaseModel):
    detected: bool = False
    operational: bool = False
    why_not_operational: list[str] = []

    def __repr__(self):
        return f"PowerSwitchStatus(detected={self.detected}, operational={self.operational}, " + \
            f"why_not_operational={self.why_not_operational})"


class DliPowerSwitch(Component):

    NUM_OUTLETS: int = 8

    def __init__(self, hostname: str, ipaddr: str | None, conf: PowerSwitchConfig):
        Component.__init__(self)
        self.hostname = hostname
        self.ipaddr = ipaddr
        self.conf = conf
        self.fqdn = self.hostname + "." + Const.WEIZMANN_DOMAIN
        self._detected = False
        self.auth = httpx.DigestAuth("admin", "1234")
        self.headers = {
            "X-CSRF": "x",
            "Accept": "application/json",
        }
        if not ipaddr:
            try:
                self.ipaddr = socket.gethostbyname(self.hostname)
            except socket.gaierror:
                raise

        self.timeout = 1
        self.base_url = f"http://{self.ipaddr}/"

        self.lock = Lock()
        self.max_age_seconds = 30  # seconds
        self.outlet_names = list(self.conf.outlets.values())

        from common.utils import RepeatTimer

        self.timer = RepeatTimer(5, function=self.on_timer)
        self.timer.name = "power-switch-timer-thread"
        self.timer.start()
        self.probe()

        if self.detected:
            self.upload_outlet_names()

    def probe(self):
        if not self.detected:
            result = self.get("restapi/relay/outlets/0/state/")
            self._detected = not (isinstance(result, dict) and "error" in result)

            if self.detected:
                logger.info(f"{self} detected")
                self.upload_outlet_names()

    def on_timer(self):
        self.probe()

    def __repr__(self):
        return f"[{self.name}:{self.ipaddr}]"

    @property
    def detected(self) -> bool:
        return self._detected

    def get(self, url: str, params: dict | None = None) -> dict | object:
        url = self.base_url + url

        with httpx.Client(trust_env=False, auth=self.auth) as client:
            try:
                # logger.info(f"GET {url=}")
                response = client.get(url=url, params=params, timeout=self.timeout)
                self._detected = True
            except httpx.TimeoutException:
                # logger.error(f"timeout after {self.timeout} seconds, {url=}")
                self._detected = False
                return {"error": "timeout"}
            except Exception as e:
                # logger.error(f"exception: {e}")
                self._detected = False
                return {"error": f"{e}"}
        return self.common_get_put(response)

    def put(self, url: str, data: str | dict | None = None) -> object:
        url = self.base_url + url

        with httpx.Client(trust_env=False, auth=self.auth) as client:
            try:
                # logger.info(f"PUT {url=}, {data=}")
                request_data = {"value": data} if isinstance(data, str) else data
                response = client.put(
                    url=url, headers=self.headers, data=request_data, timeout=self.timeout
                )
                self._detected = True
            except httpx.TimeoutException:
                # logger.error(f"timeout after {self.timeout} seconds, {url=}")
                self._detected = False
                return {"error": "timeout"}
            except Exception as e:
                logger.error(f"exception: {e}")
                self._detected = False
                return {"error": f"{e}"}

        return self.common_get_put(response)

    @staticmethod
    def common_get_put(response: httpx.Response) -> object:

        try:
            response.raise_for_status()
            s = response.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}"
            )
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error (url={e.request.url}): {e}")
            return None
        except JSONDecodeError:
            # on PUT requests, even though we give the right 'value' and the switch acts upon it
            #  (changes the outlet name) - we get a JSONDecodeError
            return None
        except Exception as e:
            logger.error(f"httpx: Exception: {e}")
            return None

        return s

    def get_outlet_state(self, outlet_name: str) -> TriStateBool:
        if not self.detected:
            return None
        try:
            idx = self.outlet_names.index(outlet_name)
        except ValueError:
            raise

        result = self.get(f"restapi/relay/outlets/{idx}/state/")
        if isinstance(result, dict) and "error" in result:
            return None
        return bool(result) if result is not None else None

    def upload_outlet_names(self):
        """
        Uploads the outlet names, as configured
        """
        for idx in range(len(self.outlet_names)):
            # self.put(f'restapi/relay/outlets/{idx}/name/', data=json.dumps({'value': self.outlet_names[idx]}))
            self.put(
                f"restapi/relay/outlets/{idx}/name/", data=f"{self.outlet_names[idx]}"
            )

    def set_outlet_state(self, outlet_name: str, state: bool):
        if not self.detected:
            return

        try:
            idx = self.outlet_names.index(outlet_name)
        except ValueError:
            raise

        self.put(url=f"restapi/relay/outlets/{idx}/state/", data={"value": state})

    def toggle_outlet(self, outlet_name: str):
        if not self.detected:
            return

        current_state = self.get_outlet_state(outlet_name)
        new_state = not current_state
        self.set_outlet_state(outlet_name, state=new_state)

    def startup(self):
        pass

    def shutdown(self):
        pass

    def abort(self):
        pass

    @property
    def why_not_operational(self) -> list[str]:
        errors = []
        if not self.detected:
            errors.append(f"power-switch: {self} not detected")
        return errors

    @property
    def operational(self) -> bool:
        return self.detected

    def status(self) -> PowerSwitchStatus:
        return PowerSwitchStatus(detected=self.detected,
                                 operational=self.operational,
                                 why_not_operational=self.why_not_operational)

    @property
    def name(self):
        return self.hostname

    @property
    def was_shut_down(self) -> bool:
        return False

    @property
    def connected(self) -> bool:
        return False

class PowerSwitchFactory:
    _instances = {}

    @classmethod
    def get_instance(cls, name: str | None = None) -> DliPowerSwitch:
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
        from common.utils import function_name

        op = function_name()

        from common.utils import canonic_unit_name

        power_switch_config: PowerSwitchConfig | None = None
        power_switch_name = None
        if name is None:
            unit_name = socket.gethostname()
            power_switch_name = unit_name.replace("mast", "mastps")
            power_switch_config = Config().get_unit(unit_name).power_switch
        else:
            unit_name = canonic_unit_name(name)
            if unit_name is not None:
                power_switch_name = unit_name.replace("mast", "mastps")
                power_switch_config = Config().get_unit(unit_name).power_switch
            elif (name.startswith("mast-spec-ps") and name[len("mast-spec-ps") :].isdigit()):
                power_switch_name = name
                power_switch_config = Config().get_specs().power_switch[power_switch_name]

        if not power_switch_name or not power_switch_config:
            raise ValueError(f"{op}: Could not determine the power switch name and configuration for '{name=}'")

        ipaddr = power_switch_config.network.ipaddr
        if not ipaddr:
            try:
                # try to GAI solve the name
                ipaddr = socket.gethostbyname(power_switch_name)
            except socket.gaierror:
                with contextlib.suppress(socket.gaierror):
                    # try to GAI solve the fully qualified name
                    ipaddr = socket.gethostbyname(power_switch_name + "." + Const.WEIZMANN_DOMAIN)

        if ipaddr is None:
            raise ValueError(f"cannot get 'ipaddr' for '{power_switch_name}")

        # We have an 'ipaddr'
        if ipaddr not in cls._instances:
            # we don't have an instance for this 'ipaddr', make a new one
            cls._instances[ipaddr] = DliPowerSwitch(hostname=power_switch_name, ipaddr=ipaddr, conf=power_switch_config)

        return cls._instances[ipaddr]

    def __init__(self):
        pass


class PowerStatus(BaseModel):
    powered: bool = False


class OutletDomain(IntFlag):
    UnitOutlets = auto()
    SpecOutlets = auto()
    UnnamedOutlets = auto()


class SwitchedOutlet:

    valid_names = {
        OutletDomain.UnitOutlets: [
            "Mount",
            "Stage",
            "Camera",
            "CameraUSB",
            "Focuser",
            "Covers",
            "Computer",
        ],
        OutletDomain.SpecOutlets: [
            "ThArWheel",
            "ThArLamp",
            "qThWheel",
            "qThLamp",
            "DeepspecU",
            "DeepspecG",
            "DeepspecR",
            "DeepspecI",
            "Highspec",
            "Chiller",
            "Stage",
            "DeepShutter",
            "HighShutter",
        ],
        OutletDomain.UnnamedOutlets: [
            "Outlet1",
            "Outlet2",
            "Outlet3",
            "Outlet4",
            "Outlet5",
            "Outlet6",
            "Outlet7",
            "Outlet8",
        ],
    }

    def __init__(  # noqa: C901
        self, domain: OutletDomain, outlet_name: str, unit_name: str | None = None, *, _from_group: bool = False
    ):
        """
        SwitchedOutlets belong to an OutletDomain and have a canonical name,
          valid within that domain.
        """
        from common.utils import function_name

        op = function_name()

        if outlet_name not in SwitchedOutlet.valid_names[domain] \
                and outlet_name not in SwitchedOutlet.valid_names[OutletDomain.UnnamedOutlets]:
            raise ValueError(
                f"{op}: bad outlet name '{outlet_name}' for {domain=}, "
                + f"not in {SwitchedOutlet.valid_names[domain]} or "
                + f"{SwitchedOutlet.valid_names[OutletDomain.UnnamedOutlets]}"
            )

        self.power_switch: DliPowerSwitch | None = None
        self.outlet_names = [outlet_name]
        self.outlets = [self]
        self.group_name = None

        try:
            self.power_switch = self.get_power_switch(
                domain=domain,
                outlet_names=self.outlet_names,
                unit_name=unit_name
            )
        except ValueError as e:
            logger.error(f"{op}: {e}")
            raise

        self.delay_after_on = (self.power_switch.conf.delay_after_on if self.power_switch else 0)

    @property
    def is_outlet_group(self) -> bool:
        """
        Returns True if this SwitchedOutlet is a group of outlets, i.e. it has multiple outlets.
        """
        return self.group_name is not None

    @classmethod
    def group(cls,
              group_name: str,
              domain: OutletDomain,
              outlet_names: list[str],
              unit_name: str | None = None):
        """
        Creates a group of outlets with the given group name and outlet names.
        """
        if len(outlet_names) < 1:
            raise ValueError("Cannot create a group with no outlets")

        for name in outlet_names:
            if name not in cls.valid_names[domain] and name not in cls.valid_names[OutletDomain.UnnamedOutlets]:
                raise ValueError(
                    f"Outlet name '{name}' is not valid for {domain=}, "
                    + f"not in {cls.valid_names[domain]} or {cls.valid_names[OutletDomain.UnnamedOutlets]}"
                )

        try:
            obj = cls(domain=domain, outlet_name=outlet_names[0], unit_name=unit_name, _from_group=True)
            obj.group_name = group_name
            obj.outlet_names = outlet_names

            try:
                obj.power_switch = cls.get_power_switch(
                    domain=domain,
                    outlet_names=obj.outlet_names,
                    unit_name=unit_name
                )
            except ValueError as e:
                logger.error(f"group: {e}")
                raise

            obj.outlets = [cls(domain, name, unit_name) for name in outlet_names]
        except ValueError as e:
            raise ValueError(f"Cannot create group '{group_name}' with {outlet_names=}: {e}") from e

        obj.delay_after_on = (obj.power_switch.conf.delay_after_on if obj.power_switch else 0)
        return obj

    @staticmethod
    def get_power_switch(domain: OutletDomain, outlet_names: list[str], unit_name: str | None = None) -> DliPowerSwitch:
        """
        Gets the DliPowerSwitch instance for the given:
        - domain (either OutletDomain.Unit or OutletDomain.Spec),
        - outlet name(s), and
        - unit name

        NOTES:
        - If the domain is OutletDomain.Unit, the unit's power switch is returned
        - If the domain is OutletDomain.Spec, the power switch containing ALL the specified outlet names is returned.
        - If no power switch can be determined, a ValueError is raised.
        """
        from common.utils import function_name

        op = function_name()

        if domain == OutletDomain.UnitOutlets:
            if unit_name is None:
                unit_name = socket.gethostname()
            return PowerSwitchFactory.get_instance(name=unit_name)
        elif domain == OutletDomain.SpecOutlets:
            conf = Config().get_specs().power_switch
            for switch_name in conf:
                if all([outlet_name in conf[switch_name].outlets.values() for outlet_name in outlet_names]):
                    return PowerSwitchFactory.get_instance(name=switch_name)

        raise ValueError(f"{op}: Cannot create power switch for {domain=}, {outlet_names=}, {unit_name=}")

    def __repr__(self):
        ret = "SwitchedOutlet("
        if self.is_outlet_group:
            ret += f"group='{self.group_name}', "
        ret += f"switch={self.power_switch}, "
        ret += f"outlets={self.outlet_names}" if self.is_outlet_group else f"outlet='{self.outlet_names[0]}'"
        ret += ")"
        return ret

    @property
    def name(self) -> str:
        return self.group_name if (self.group_name and self.is_outlet_group) else self.outlet_names[0]

    @property
    def state(self) -> TriStateBool:
        if self.power_switch is None:
            return None

        return all([self.power_switch.get_outlet_state(name) for name in self.outlet_names])

    def power_on_or_off(self, new_state: bool):
        from common.utils import function_name

        op = function_name()

        if self.power_switch is None or not self.power_switch.detected:
            logger.error(f"{op}: {self.outlet_names=}: {self.power_switch} not detected")
            return

        # current_states = [self.power_switch.get_outlet_state(name) for name in self.outlet_names]
        current_states = [outlet.state for outlet in self.outlets]
        if any(state != new_state for state in current_states):
            for name in self.outlet_names:
                self.power_switch.set_outlet_state(name, new_state)
            if new_state is True and self.delay_after_on:
                logger.info(
                    f"{op}: delaying {self.delay_after_on} sec. after powering ON  ({self})"
                )
                time.sleep(self.delay_after_on)

    def power_on(self):
        self.power_on_or_off(True)

    def power_off(self):
        self.power_on_or_off(False)

    def toggle(self):
        if self.power_switch is None:
            return
        for name in self.outlet_names:
            self.power_switch.toggle_outlet(name)

    def cycle(self):
        if self.is_on():
            self.power_off()
            time.sleep(3)
            self.power_on()
        else:
            self.power_on()

    def is_on(self) -> bool:
        if self.power_switch is None:
            return False
        return all(outlet.state for outlet in self.outlets)

    def is_off(self) -> bool:
        if self.power_switch is None:
            return True
        return all(not outlet.state for outlet in self.outlets)

    def power_status(self) -> PowerStatus:
        return PowerStatus(powered=self.is_on())

    def populate(self, target: object):
        """
        Populates the target object with our attributes and methods

        Use-case:
        - SwitchedOutlet.group(...).populate(self) in an object's constructor inheriting from SwitchedOutlet
        """
        for key, value in self.__dict__.items():
            setattr(target, key, value)
        return target


if __name__ == "__main__":
    o8 = SwitchedOutlet(
        domain=OutletDomain.UnitOutlets, unit_name="mastw", outlet_name="Outlet8"
    )
    print(f"Original: {o8}")
    o8.toggle()
    print(f"After toggle: {o8}")

    g = SwitchedOutlet.group(group_name="Camera", domain=OutletDomain.UnitOutlets,
                             outlet_names=["Camera", "CameraUSB"])
    print(f"{g}, is_on: {g.is_on()}")
    g.toggle()
    print(f"{g}, is_on: {g.is_on()}")
    if g.is_on():
        g.power_off()
    else:
        g.power_on()
    print(f"{g}, is_on: {g.is_on()}")
