import asyncio
import datetime
from gc import enable
from logging import Logger

import humanfriendly
from pydantic import BaseModel

from common.api import SafetyApi
from common.config import Config
from common.mast_logging import init_log
from common.utils import fromisoformat_zulu


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
    settings: (
        SunElevationSettingsModel
        | MinMaxSettingsModel
        | HumanInterventionSettingsModel
        | None
    ) = None
    last_update: str | None = None  # ISO-8601 Zulu
    readings: list[SensorReadingModel] = []
    reasons_for_not_safe: list[str] | None = None
    started_settling: datetime.datetime | None = None


class SafetySensorModel(BaseModel):
    project: str
    sensor: SensorModel
    interval: float


logger = Logger("mast-common-safety")
init_log(logger)


def safety_get_sensor(
    sensor_name: str,
    project_name: str | None = None,
    timeout: float = 1.0,
    max_age: datetime.timedelta = datetime.timedelta(seconds=3),
) -> tuple[float, bool, list[str] | None] | None:
    """
    Fetches a sensor reading from the safety system.
    - `sensor_name`: the name of the sensor to fetch (e.g. "wind-speed", "humidity", "dew-point")
    - `project_name`: the project name to use (if None, uses the local site's project name)
    - `timeout`: the timeout in seconds to use for the API call
    - `max_age`: the maximum age of the sensor reading to consider it valid

    Returns a tuple of (`value`, `is_safe`, `reasons_for_not_safe`) or None if there were errors

    """
    ret = None

    if project_name is None:
        local_site = Config().local_site
        assert local_site is not None
        project_name = local_site.project

    try:
        safety_api = SafetyApi(ipaddr="10.23.1.25", port=8001, timeout=timeout)
        response = asyncio.run(safety_api.get(f"{project_name}/sensor/{sensor_name}"))
        if response.succeeded and response.value is not None:
            sensor: SensorModel = SensorModel(**response.value["sensor"])

            if not isinstance(sensor.readings, list):
                sensor.readings = [sensor.readings]

            latest_reading = sensor.readings[-1]
            age = datetime.datetime.now(datetime.UTC) - fromisoformat_zulu(
                latest_reading.time
            )
            if age > max_age:
                logger.warning(
                    f"safety_get_sensor: ignoring '{sensor.name}' reading, too old '{humanfriendly.format_timespan(age)}' > '{humanfriendly.format_timespan(max_age)}'"
                )
                return None
            else:
                return latest_reading.value, sensor.safe, sensor.reasons_for_not_safe
        else:
            for error in response.errors or []:
                logger.error(error)
            return None
    except Exception as ex:
        return None


if __name__ == "__main__":
    for sensor_name in ["wind-speed", "humidity", "dew-point", "sun"]:
        result = safety_get_sensor(
            sensor_name, timeout=60, max_age=datetime.timedelta(minutes=10)
        )

        if result is not None:
            value, is_safe, reasons = result
            print(f"{sensor_name}: {value=}, {is_safe=}, {reasons=}")
