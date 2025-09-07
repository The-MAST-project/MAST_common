import asyncio
import logging
import re
import socket
from datetime import UTC, datetime
from enum import Enum, auto
from xml import dom

import httpx
import humanfriendly

from common.canonical import CanonicalResponse
from common.config import Config
from common.config.site import Site
from common.const import Const
from common.mast_logging import init_log
from common.utils import function_name

logger = logging.getLogger("api")
init_log(logger)


class ApiDomain(Enum):
    Unit = auto()
    Spec = auto()
    Control = auto()
    Safety = auto()


api_devices = {
    ApiDomain.Unit: ["mount", "focuser", "camera", "stage", "covers"],
    ApiDomain.Spec: [],
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
                value = [
                    ApiResponse(item) if isinstance(item, dict) else item
                    for item in value
                ]
            setattr(self, key, value)

    def __repr__(self):
        attrs = ", ".join(f"{key}={value!r}" for key, value in self.__dict__.items())
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

    def __init__(  # noqa: C901
        self,
        hostname: str | None = None,
        ipaddr: str | None = None,
        port: int | None = 8000,
        domain: ApiDomain | None = None,
        device: str | None = None,
        timeout: float | None = TIMEOUT,
    ):
        if hostname is None and ipaddr is None:
            raise ValueError("both 'hostname' and 'ipaddr' are None")

        if ipaddr is not None and domain is None:
            raise ValueError(
                "if 'ipaddr' is provided a 'domain' must be provided as well"
            )

        domain_base = None

        if ipaddr is not None:
            self.domain = domain
            self.ipaddr = ipaddr
            domain_base = ""
            if domain == ApiDomain.Safety:
                pass
            elif domain == ApiDomain.Unit:
                domain_base = Const.BASE_UNIT_PATH
            elif domain == ApiDomain.Spec:
                domain_base = Const.BASE_SPEC_PATH
            elif domain == ApiDomain.Control:
                domain_base = Const.BASE_CONTROL_PATH
        else:
            if hostname is None:
                raise ValueError("if 'ipaddr' is None, 'hostname' must be provided")

            if hostname.endswith("-spec"):
                self.domain = ApiDomain.Spec
                domain_base = Const.BASE_SPEC_PATH
            elif hostname.endswith("-control"):
                self.domain = ApiDomain.Control
                domain_base = Const.BASE_CONTROL_PATH
            else:
                mast_pattern = re.compile(r"^mast(0[1-9]|1[0-9]|20|w)$")
                if mast_pattern.match(hostname):
                    self.domain = ApiDomain.Unit
                    domain_base = Const.BASE_UNIT_PATH

            self.hostname = hostname
            try:
                self.ipaddr = socket.gethostbyname(hostname)
            except socket.gaierror:
                try:
                    self.ipaddr = socket.gethostbyname(
                        hostname + "." + Const.WEIZMANN_DOMAIN
                    )
                except socket.gaierror as err:
                    raise ValueError(f"cannot get 'ipaddr' for {hostname=}") from err

        if self.ipaddr is not None and hostname is None:
            try:
                hostname, _, _ = socket.gethostbyaddr(self.ipaddr)
                self.hostname = hostname
            except socket.herror:
                self.hostname = None

        self.base_url = f"http://{self.ipaddr}:{port}{domain_base}"
        if device:
            if self.domain is None:
                raise ValueError("domain cannot be None when device is specified")
            if device in api_devices[self.domain]:
                self.base_url += f"/{device}"
            else:
                raise Exception(
                    f"bad {device=} for domain {self.domain}, allowed: {api_devices[self.domain]}"
                )

        self.detected = False
        self.timeout = timeout
        self.errors = []

    @property
    def operational(self) -> bool:
        return len(self.errors) == 0

    async def get(self, method: str, params: dict | None = None):
        url = f"{self.base_url}/{method}"
        op = f"{function_name()}, {url=}"
        self.errors = []
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                response = await client.get(
                    url=url, params=params, timeout=self.timeout
                )

            except httpx.TimeoutException:
                self.errors.append(
                    f"{op}: timeout after {self.timeout} seconds, {url=}"
                )
                self.detected = False
                return CanonicalResponse(errors=self.errors)

            except Exception as e:
                self.errors.append(f"{op}: exception: {e}")
                self.detected = False
                return CanonicalResponse(errors=self.errors)

        return self.common_get_put(response)

    async def put(
        self,
        method: str,
        params: dict | None = None,
        data: dict | None = None,
        json: dict | None = None,
    ):
        url = f"{self.base_url}/{method}"
        op = f"{function_name()}, {url=}"
        self.errors = []
        async with httpx.AsyncClient(trust_env=False) as client:
            try:
                response = await client.put(
                    url=f"{self.base_url}/{method}",
                    headers={"Content-Type": "application/json"},
                    params=params,
                    data=data,
                    json=json,
                    timeout=self.timeout,
                )
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

    def _handle_canonical_response(self, canonical_response, op):
        if (
            hasattr(canonical_response, "exception")
            and canonical_response.exception is not None
        ):
            e = canonical_response.exception
            self.append_error(f"{op}: Remote Exception     type: {e.type}")
            self.append_error(f"{op}: Remote Exception  message: {e.message}")
            for arg in e.args:
                self.append_error(f"{op}: Remote Exception      arg: {arg}")
            if e.traceback:
                for line in e.traceback.split("\n"):
                    self.append_error(f"{op}: Remote Exception traceback: {line}")
                return None

        if (
            hasattr(canonical_response, "errors")
            and canonical_response.errors is not None
        ):
            for err in canonical_response.errors:
                self.append_error(err)
            return None

        if (
            hasattr(canonical_response, "value")
            and canonical_response.value is not None
        ):
            return canonical_response.value

        self.append_error(
            f"{op}: got a canonical response but fields "
            + "'exception', 'errors' and 'value' are all None"
        )
        return None

    def common_get_put(self, response: httpx.Response):
        value = None
        op = function_name()

        try:
            response.raise_for_status()
            response_dict = response.json()
            self.detected = True

            if "api_version" in response_dict and response_dict["api_version"] == "1.0":
                canonical_response = CanonicalResponse(**response_dict)
                value = self._handle_canonical_response(canonical_response, op)
                if value is None and self.errors:
                    return CanonicalResponse(errors=self.errors)
            else:
                value = response_dict
                logger.warning(
                    f"{op}: received NON canonical response, returning it as 'value'"
                )

        except httpx.HTTPStatusError as e:
            self.append_error(
                f"HTTP error (url={e.request.url}): {e.response.status_code} - {e.response.text}"
            )
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
    def __init__(
        self,
        hostname: str | None = None,
        ipaddr: str | None = None,
        domain: ApiDomain | None = None,
        device: str | None = None,
    ):
        super().__init__(hostname=hostname, ipaddr=ipaddr, device=device, domain=domain)


class SpecApi(ApiClient):
    def __init__(self, site_name: str | None = None):
        self.client = None

        if site_name:
            site = [s for s in Config().sites if s.name == site_name][0]
        else:
            site: Site | None = Config().local_site
        service_conf = Config().get_service(service_name="spec")
        if service_conf is None:
            logger.error("Spec service configuration not found")
            return
        port = service_conf.port
        assert site is not None
        super().__init__(hostname=f"{site.project}-{site.name}-spec", port=port)


class ControllerApi:
    def __init__(self, site_name: str | None = None):
        self.client = None

        if site_name:
            site = [s for s in Config().sites if s.name == site_name][0]
        else:
            site: Site | None = Config().local_site
        service_conf = Config().get_service(service_name="control")
        if service_conf is None:
            logger.error("Control service configuration not found")
            return
        port = service_conf.port
        try:
            assert site is not None
            self.client = ApiClient(f"{site.project}-{site.name}-control", port=port)
        except ValueError as e:
            logger.error(f"{e}")


class SafetyApi(ApiClient):
    def __init__(
        self,
        site_name: str | None = None,
        hostname: str | None = None,
        ipaddr: str | None = None,
        port: int | None = None,
        timeout: float | None = 0.5,
    ):
        self.client = None

        if site_name:
            site = [s for s in Config().sites if s.name == site_name][0]
        else:
            site: Site = Config().local_site
        service_conf = Config().get_service(service_name="safety")

        if port is None and service_conf is not None:
            port = service_conf.port

        if ipaddr is None:
            if hostname is None:
                hostname = f"{site.project}-{site.name}-safety"
            try:
                ipaddr = socket.gethostbyname(hostname)
            except socket.gaierror as err:
                raise ValueError(f"cannot get 'ipaddr' for {hostname=}") from err

        super().__init__(
            ipaddr=ipaddr, port=port, timeout=timeout, domain=ApiDomain.Safety
        )


def test_bogus_unit_api():
    try:
        unit = ApiClient(hostname="mast01")
        response = unit.get("status")
        if response:
            print(f"unit.status(): {response=}")
    except Exception as e:
        logger.error(f"Error accessing unit API: {e}")
        pass

    try:
        focuser = ApiClient(hostname="mast01", device="focuser")
        response = focuser.get("status")
        if response:
            print(f"focuser.status(): {response=}")
    except Exception as e:
        logger.error(f"Error accessing unit API: {e}")
        pass

    try:
        ApiClient(hostname="mast01", device="screwdriver")
    except Exception as ex:
        print(f"exception: {ex}")


def test_safety_wind_speed():
    from common.utils import fromisoformat_zulu

    try:
        safety = SafetyApi(ipaddr="10.23.1.25", port=8001, timeout=10)
        url = "mast/sensor/wind-speed"
        response: CanonicalResponse = asyncio.run(safety.get(url))
        if response.succeeded and response.value is not None:
            if (
                "sensor" in response.value
                and "readings" in response.value["sensor"]
                and isinstance(response.value["sensor"]["readings"], list)
            ):
                readings = response.value["sensor"]["readings"]
                if len(readings) > 0:
                    latest_reading = readings[-1]
                    wind_speed = latest_reading["value"]
                    logger.info(f"wind speed tstamp:'{latest_reading['time']}'")
                    age = datetime.now(UTC) - fromisoformat_zulu(latest_reading["time"])

            print(
                f"{wind_speed=}, age='{humanfriendly.format_timespan(age.total_seconds())}'"
            )

    except Exception as e:
        logger.error(f"Error accessing safety API: {e}")
        pass


def test_safety_sensors():
    import json

    try:
        safety = SafetyApi(ipaddr="10.23.1.25", port=8001, timeout=10)
        url = "mast/sensors"
        response: CanonicalResponse = asyncio.run(safety.get(url))
        if response.succeeded and response.value is not None:
            print(json.dumps(response.value, indent=2))

    except Exception as e:
        logger.error(f"Error accessing safety API: {e}")
        pass


if __name__ == "__main__":
    test_safety_sensors()
