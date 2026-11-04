import asyncio
import datetime

import humanfriendly
from pydantic import BaseModel

from common.api import SafetyApi
from common.mast_logging import get_logger


class SensorSettingsBaseModel(BaseModel):
    enabled: bool
    project: str
    source: str
    station: str
    datum: str
    nreadings: int = 1


class SensorReadingModel(BaseModel):
    time: str  # ISO-8601 Zulu timestamp
    value: float


class SunElevationSettingsModel(SensorSettingsBaseModel):
    dawn: float | None = None
    dusk: float | None = None


class MinMaxSettingsModel(SensorSettingsBaseModel):
    min: float | None = None
    max: float | None = None
    settling: float | None = None


class HumanInterventionSettingsModel(SensorSettingsBaseModel):
    human_intervention_file: str | None = None


class SensorModel(BaseModel):
    name: str
    station: str | None = None
    safe: bool
    settings: SunElevationSettingsModel | MinMaxSettingsModel | HumanInterventionSettingsModel | None = None
    last_update: str | None = None  # ISO-8601 Zulu
    readings: list[SensorReadingModel] = []
    reasons_for_not_safe: list[str] | None = None
    started_settling: datetime.datetime | None = None


class SafetySensorModel(BaseModel):
    project: str
    sensor: SensorModel
    interval: float


logger = get_logger(__name__)


def safety_get_sensor(
    weather_station_name: str,
    sensor_name: str,
    timeout: float = 1.0,
    max_age: datetime.timedelta = datetime.timedelta(seconds=3),
) -> float | None:
    """One sensor's latest value from the observatory's data service, or None.

    - `weather_station_name`: the device, e.g. "davis" (`/observatory/data/api/devices` lists them)
    - `sensor_name`: its sensor, e.g. "wind_speed", "outside_humidity", "dew_point"
      (`/observatory/data/api/device/<device>/sensors` lists them)
    - `timeout`: seconds for the request
    - `max_age`: a reading older than this is ignored

    None when the reading cannot be had or is too old, logged. The service gives values, not
    a safety verdict: there is no `is_safe` or `reasons_for_not_safe` any more.
    """
    op = "safety_get_sensor"
    try:
        safety_api = SafetyApi(ipaddr="10.23.1.25", port=80, timeout=timeout)
        result = asyncio.run(safety_api.get(device=weather_station_name, sensor=sensor_name, timeout=timeout))
        if result is None:
            for error in safety_api.errors:
                logger.error(f"{op}: {error}")
            return None

        value, age = result
        if age > max_age:
            logger.warning(
                f"{op}: ignoring {weather_station_name}/{sensor_name}, too old "
                f"'{humanfriendly.format_timespan(age.total_seconds())}' > "
                f"'{humanfriendly.format_timespan(max_age.total_seconds())}'"
            )
            return None
        return value
    except Exception:
        # Broad on purpose -- a sensor read must never take the caller down -- but it
        # used to return None in silence, so a malformed reading looked exactly like a
        # sensor that had nothing to say. logger.exception keeps the traceback.
        logger.exception(f"{op}: failed")
        return None


if __name__ == "__main__":
    for station, sensor_name in [("davis", "wind_speed"), ("davis", "outside_humidity"), ("davis", "dew_point")]:
        value = safety_get_sensor(station, sensor_name, timeout=60, max_age=datetime.timedelta(minutes=10))
        print(f"{station}/{sensor_name}: {value=}")
