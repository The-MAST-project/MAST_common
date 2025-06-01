import contextlib
import logging
import socket
import time
from enum import IntFlag, auto
from json import JSONDecodeError
from threading import Lock

import httpx
from pydantic import BaseModel

from common.components import Component
from common.config import Config
from common.mast_logging import init_log
from common.networking import WEIZMANN_DOMAIN
from common.utils import RepeatTimer, canonic_unit_name, function_name

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

    def __init__(self, hostname: str, ipaddr: str | None, conf: dict):
        Component.__init__(self)
        self.hostname = hostname
        self.ipaddr = ipaddr
        self.conf = conf
        self.fqdn = self.hostname + "." + WEIZMANN_DOMAIN
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
        self.outlet_names = list(self.conf["outlets"].values())

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
        op = function_name()

        conf = None
        ps_name = None
        if name is None:
            unit_name = socket.gethostname()
            ps_name = unit_name.replace("mast", "mastps")
            conf = Config().get_unit(unit_name)["power_switch"]
        else:
            unit_name = canonic_unit_name(name)
            if unit_name is not None:
                ps_name = unit_name.replace("mast", "mastps")
                conf = Config().get_unit(unit_name)["power_switch"]
            elif (
                name.startswith("mast-spec-ps")
                and name[len("mast-spec-ps") :].isdigit()
            ):
                ps_name = name
                conf = Config().get_specs()["power_switch"][name]

        if not ps_name:
            raise ValueError(f"{op}: Bad name '{name}'")

        ipaddr = None
        try:
            # try to GAI solve the name
            ipaddr = socket.gethostbyname(ps_name)
        except socket.gaierror:
            with contextlib.suppress(socket.gaierror):
                # try to GAI solve the fully qualified name
                ipaddr = socket.gethostbyname(ps_name + "." + WEIZMANN_DOMAIN)

        if ipaddr is None:
            # We could not GAI resolve the name, maybe it's in the configuration database
            conf = Config().get_specs()["power_switch"]
            if ps_name in conf and "ipaddr" in conf[ps_name]["network"]:
                ipaddr = conf[ps_name]["network"]["ipaddr"]
                conf = conf[ps_name]

        if ipaddr is None:
            raise ValueError(f"cannot get 'ipaddr' for '{ps_name}")

        # We have an 'ipaddr'
        if ipaddr not in cls._instances:
            # we don't have an instance for this 'ipaddr', make a new one
            config_dict = conf if isinstance(conf, dict) else {}
            cls._instances[ipaddr] = DliPowerSwitch(
                hostname=ps_name, ipaddr=ipaddr, conf=config_dict
            )

        return cls._instances[ipaddr]

    def __init__(self):
        pass


class PowerStatus(BaseModel):
    powered: bool = False


class OutletDomain(IntFlag):
    Unit = auto()
    Spec = auto()
    Unnamed = auto()


class SwitchedOutlet:

    valid_names = {
        OutletDomain.Unit: [
            "Mount",
            "Stage",
            "Camera",
            "Focuser",
            "Covers",
            "Computer",
        ],
        OutletDomain.Spec: [
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
        OutletDomain.Unnamed: [
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

    def __init__(
        self, domain: OutletDomain, outlet_name: str, unit_name: str | None = None
    ):
        """
        SwitchedOutlets belong to an OutletDomain and have a canonical name,
          valid within that domain.
        """
        op = function_name()

        self.power_switch: DliPowerSwitch | None = None
        self.outlet_name = outlet_name

        if (
            self.outlet_name not in SwitchedOutlet.valid_names[domain]
            and self.outlet_name not in SwitchedOutlet.valid_names[OutletDomain.Unnamed]
        ):
            raise ValueError(
                f"{op}: bad outlet name '{self.outlet_name}' for {domain=}, "
                + f"not in {SwitchedOutlet.valid_names[domain]} or "
                + f"{SwitchedOutlet.valid_names[OutletDomain.Unnamed]}"
            )

        if domain == OutletDomain.Unit:
            # Unit outlets have always the same names but the socket number may differ from unit to unit
            if unit_name is None:
                unit_name = socket.gethostname()
            try:
                self.power_switch = PowerSwitchFactory.get_instance(name=unit_name)
            except ValueError:
                raise
            try:
                conf = Config().get_unit(unit_name=unit_name)["power_switch"]
                if self.outlet_name not in conf["outlets"].values():
                    raise ValueError(
                        f"outlet name '{self.outlet_name}' not in {list(conf['outlets'].values())}"
                    )
            except:
                raise

        elif domain == OutletDomain.Spec:
            # Spec outlets have pre-defined names but may belong to any one of the spec power switches
            conf = Config().get_specs()["power_switch"]
            for switch_name in conf:
                if self.outlet_name in conf[switch_name]["outlets"].values():
                    # we located the switch
                    self.power_switch = PowerSwitchFactory.get_instance(
                        name=switch_name
                    )

        self.delay_after_on = (
            self.power_switch.conf.get("delay_after_on", 0) if self.power_switch else 0
        )

    def __repr__(self):
        # logger.info(f"__repr__: {self.outlet_name=}")
        return f"{self.power_switch}:{self.outlet_name}"

    @property
    def name(self) -> str:
        return self.outlet_name

    @property
    def state(self) -> TriStateBool:
        # self.power_switch.fetch_outlets()
        # return [o.state for o in self.power_switch.outlets if o.label == self.outlet_name][0]
        if self.power_switch is None:
            return None
        return self.power_switch.get_outlet_state(self.outlet_name)

    def power_on_or_off(self, new_state: bool):
        op = function_name()

        if self.power_switch is None or not self.power_switch.detected:
            logger.error(f"{op}: {self.outlet_name=}: {self.power_switch} not detected")
            return

        current_state = self.power_switch.get_outlet_state(self.outlet_name)
        if current_state != new_state:
            self.power_switch.set_outlet_state(self.outlet_name, new_state)
            if new_state is True and self.delay_after_on:
                logger.info(
                    f"{op}: delaying {self.delay_after_on} sec. after powering ON  ({self.name})"
                )
                time.sleep(self.delay_after_on)

    def power_on(self):
        self.power_on_or_off(True)

    def power_off(self):
        self.power_on_or_off(False)

    def toggle(self):
        if self.power_switch is None:
            return
        self.power_switch.toggle_outlet(self.outlet_name)

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
        state = self.power_switch.get_outlet_state(self.outlet_name)
        return state is True

    def is_off(self) -> bool:
        if self.power_switch is None:
            return True
        state = self.power_switch.get_outlet_state(self.outlet_name)
        return state is False

    def power_status(self) -> PowerStatus:
        return PowerStatus(powered=self.is_on())


if __name__ == "__main__":
    o8 = SwitchedOutlet(
        domain=OutletDomain.Unit, unit_name="mastw", outlet_name="Outlet8"
    )
    print(f"Original: {o8}")
    o8.toggle()
    print(f"After toggle: {o8}")
