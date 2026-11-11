import asyncio
import datetime
import math
import threading
from collections.abc import Callable

import humanfriendly
from pydantic import BaseModel

from common.api import SafetyApi
from common.config.safety import SafetyConfig
from common.config.site import Site
from common.mast_logging import get_logger


class SensorSettingsBaseModel(BaseModel):
    enabled: bool
    project: str
    source: str
    station: str
    datum: str


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


# --- Stabilization ---------------------------------------------------------------------------
#
# The weather service gives raw values; whether they are safe is decided here, from a per-sensor
# list of readings.  See `SensorState` and `Safety` for the rules.

STALE_LIMIT = datetime.timedelta(seconds=10)  # a reading older than this is never stored
MAX_GAP = datetime.timedelta(seconds=15)  # longest tolerated silence, inside a window or at its edges
POLL_INTERVAL = datetime.timedelta(seconds=5)  # how often the owner (MAST_control) should poll
READING_TOLERANCE = datetime.timedelta(milliseconds=500)  # closer than this to the last one: same reading

UTC = datetime.UTC


class SafetyVerdictModel(BaseModel):
    """The answer to "is it safe?": `reasons` is present only when it is not."""

    is_safe: bool
    reasons: list[str] | None = None


def _fmt(seconds: float) -> str:
    return humanfriendly.format_timespan(round(seconds))


def _iso(when: datetime.datetime) -> str:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class SensorState:
    """One windowed sensor: its raw readings, and a verdict computed from them on demand.

    The verdict is never stored, so a changed limit or window re-judges the history. A sensor is
    safe only when every one of these holds at `now`:

    - the newest reading is no more than `MAX_GAP` old;
    - no gap between consecutive readings in the window, nor between the window's start and its
      first reading, exceeds `MAX_GAP`  (so the window really was observed, end to end);
    - every reading in the window is within `min`..`max` (both inclusive).

    Anything less -- including no readings at all, as after a restart -- is not safe. A sensor with
    no `settling` has no window: the newest reading alone decides.

    Not thread-safe by itself; `Safety` serialises access.
    """

    def __init__(self, name: str, settings: MinMaxSettingsModel):
        self.name = name
        self.settings = settings
        self._readings: list[tuple[datetime.datetime, float]] = []
        self.last_rejection: str | None = None

    @property
    def window(self) -> datetime.timedelta:
        return datetime.timedelta(seconds=self.settings.settling or 0)

    def add_reading(self, taken: datetime.datetime, value: float, age: datetime.timedelta) -> bool:
        """Store one reading, taken at `taken` and `age` old now. False (and `last_rejection`) if refused."""
        if not math.isfinite(value):
            self.last_rejection = f"non-finite value {value!r}"
            return False
        if age > STALE_LIMIT:
            self.last_rejection = f"reading is {_fmt(age.total_seconds())} old (limit {_fmt(STALE_LIMIT.total_seconds())})"
            return False
        if age < datetime.timedelta(0):
            self.last_rejection = f"reading is from the future ({_fmt(-age.total_seconds())}), clock skew?"
            return False
        if self._readings and taken - self._readings[-1][0] < READING_TOLERANCE:
            return False  # the same reading, polled twice -- not a rejection, nothing to report
        self._readings.append((taken, value))
        self.last_rejection = None
        self._prune(taken)
        return True

    def _prune(self, now: datetime.datetime) -> None:
        if self.window <= datetime.timedelta(0):
            del self._readings[:-1]
            return
        start = now - self.window
        self._readings = [r for r in self._readings if r[0] >= start]

    def _is_ok(self, value: float) -> bool:
        s = self.settings
        return (s.min is None or value >= s.min) and (s.max is None or value <= s.max)

    def _why_not(self, value: float) -> str:
        s = self.settings
        if s.min is not None and value < s.min:
            return f"{value:g} < {s.min:g}"
        return f"{value:g} > {s.max:g}"

    def evaluate(self, now: datetime.datetime) -> SensorModel:
        s = self.settings
        reasons: list[str] = []
        started: datetime.datetime | None = None
        window = self.window
        readings = [r for r in self._readings if window <= datetime.timedelta(0) or r[0] >= now - window]

        if not s.enabled:
            reasons.append("disabled")
        elif not readings:
            reasons.append(f"no usable reading ({self.last_rejection})" if self.last_rejection else "no readings yet")
        else:
            silence = now - readings[-1][0]
            if silence > MAX_GAP:
                reasons.append(f"no reading for {_fmt(silence.total_seconds())}")
            bad = [(t, v) for t, v in readings if not self._is_ok(v)]
            if bad:
                t, v = bad[-1]
                extra = f" (+{len(bad) - 1} earlier)" if len(bad) > 1 else ""
                reasons.append(f"{self._why_not(v)} at {_iso(t)}{extra}")
            if window > datetime.timedelta(0):
                lead = readings[0][0] - (now - window)
                if lead > MAX_GAP:
                    reasons.append(
                        f"only {_fmt((now - readings[0][0]).total_seconds())} of {_fmt(window.total_seconds())} window"
                    )
                for (t0, _), (t1, _) in zip(readings, readings[1:], strict=False):
                    if t1 - t0 > MAX_GAP:
                        reasons.append(f"gap of {_fmt((t1 - t0).total_seconds())} at {_iso(t0)}")
                        break
                if reasons and not bad:
                    started = readings[0][0]  # settling: the oldest reading we trust so far

        return SensorModel(
            name=self.name,
            station=s.station,
            safe=not reasons,
            settings=s,
            last_update=_iso(readings[-1][0]) if readings else None,
            readings=[SensorReadingModel(time=_iso(t), value=v) for t, v in readings],
            reasons_for_not_safe=reasons or None,
            started_settling=started,
        )


class SunElevationState:
    """The internal sun-elevation sensor: computed, never read, and with no window.

    `elevation(when)` gives the sun's elevation in degrees at the site. While the sun is rising the
    `dawn` limit applies, while it is setting the `dusk` limit; elevation above the limit is not safe.
    A limit left unset is not applied, but at least one must be set -- a sensor that checks nothing
    must not read as safe.
    """

    NAME = "sun_elevation"

    def __init__(self, settings: SunElevationSettingsModel, elevation: Callable[[datetime.datetime], float]):
        self.name = self.NAME
        self.settings = settings
        self._elevation = elevation

    def evaluate(self, now: datetime.datetime) -> SensorModel:
        s = self.settings
        reasons: list[str] = []
        readings: list[SensorReadingModel] = []
        if not s.enabled:
            reasons.append("disabled")
        elif s.dawn is None and s.dusk is None:
            reasons.append("neither dawn nor dusk limit is set")
        else:
            try:
                elevation = self._elevation(now)
                rising = self._elevation(now + datetime.timedelta(minutes=1)) > elevation
            except Exception as e:  # noqa: BLE001 -- a failed computation must read as not safe, not crash
                reasons.append(f"cannot compute: {e!r}")
            else:
                readings.append(SensorReadingModel(time=_iso(now), value=elevation))
                limit = s.dawn if rising else s.dusk
                if limit is not None and elevation > limit:
                    reasons.append(f"{elevation:.1f} > {limit:g} ({'dawn' if rising else 'dusk'})")
        return SensorModel(
            name=self.name,
            station="internal",
            safe=not reasons,
            settings=s,
            last_update=readings[-1].time if readings else None,
            readings=readings,
            reasons_for_not_safe=reasons or None,
        )


class Safety:
    """All the sensors, and the one question the enclosure decision asks: `is_safe()`.

    `is_safe` is an in-memory evaluation -- it never touches the weather service -- so callers may
    ask as often as they like. The owner (MAST_control) feeds it from polling threads, one or
    several, every `POLL_INTERVAL` via `ingest`; one lock serialises them against the readers.

    Safe means *no enabled sensor says otherwise*: with no enabled sensors at all it is safe. That is
    a decision, not an oversight -- an observatory that has switched every check off has asked not
    to be second-guessed -- but note it also covers a site with no `safety` document at all.
    """

    def __init__(self, sun: SunElevationState | None = None):
        self.sun = sun
        self.sensors: dict[str, SensorState] = {}
        self._lock = threading.RLock()

    def add_sensor(self, name: str, settings: MinMaxSettingsModel) -> SensorState:
        with self._lock:
            self.sensors[name] = SensorState(name, settings)
            return self.sensors[name]

    def ingest(
        self,
        sensor: str,
        result: tuple[float, datetime.timedelta] | None,
        now: datetime.datetime | None = None,
    ) -> bool:
        """Feed one `SafetyApi.get` result -- `(value, age)`, or None for a failed read -- to `sensor`."""
        if result is None:
            return False
        now = now or datetime.datetime.now(UTC)
        value, age = result
        with self._lock:
            return self.sensors[sensor].add_reading(now - age, value, age)

    def report_failure(self, sensor: str, reason: str) -> None:
        """Note why `sensor` got no reading this time (it shows in the reasons while it has none)."""
        with self._lock:
            if sensor in self.sensors:
                self.sensors[sensor].last_rejection = reason

    def apply_config(self, config: "SafetyConfig | None", *, project: str, site: "Site | None" = None) -> None:
        """Make the sensors match the `safety` document (None: no sensors at all).

        Settings of a sensor that stays are replaced in place, so its readings are kept and re-judged
        against the new limits; a sensor that is gone is dropped, a new one starts empty. The sun
        sensor takes its dawn/dusk from the document and needs `site` for the coordinates.
        """
        with self._lock:
            wanted = config.sensors if config else {}
            for name in [n for n in self.sensors if n not in wanted]:
                del self.sensors[name]
            for name, limits in wanted.items():
                settings = MinMaxSettingsModel(
                    enabled=limits.enabled,
                    project=project,
                    source="davis",
                    station="davis",
                    datum=name,
                    min=limits.min,
                    max=limits.max,
                    settling=limits.settling,
                )
                if name in self.sensors:
                    self.sensors[name].settings = settings
                else:
                    self.add_sensor(name, settings)

            sun = config.sun_elevation if config else None
            if sun is None or site is None:
                self.sun = None
                return
            self.sun = SunElevationState(
                SunElevationSettingsModel(
                    enabled=sun.enabled,
                    project=project,
                    source="internal",
                    station=site.name,
                    datum=SunElevationState.NAME,
                    dawn=sun.dawn,
                    dusk=sun.dusk,
                ),
                site.sun_elevation,
            )

    def status(self, now: datetime.datetime | None = None) -> list[SensorModel]:
        """Every sensor, with its readings and verdict -- for diagnosis, not for deciding."""
        now = now or datetime.datetime.now(UTC)
        with self._lock:
            models = [state.evaluate(now) for state in self.sensors.values()]
            if self.sun:
                models.append(self.sun.evaluate(now))
        return models

    def reasons(self, now: datetime.datetime | None = None) -> list[str]:
        """Why it is not safe, as `<sensor>: <reason>` lines; empty when safe."""
        models = [m for m in self.status(now) if m.settings is None or m.settings.enabled]
        return [f"{m.name}: {r}" for m in models for r in (m.reasons_for_not_safe or [])]

    def is_safe(self, now: datetime.datetime | None = None) -> bool:
        return not self.reasons(now)

    def verdict(self, now: datetime.datetime | None = None) -> SafetyVerdictModel:
        reasons = self.reasons(now)
        return SafetyVerdictModel(is_safe=not reasons, reasons=reasons or None)


if __name__ == "__main__":
    for station, sensor_name in [("davis", "wind_speed"), ("davis", "outside_humidity"), ("davis", "dew_point")]:
        value = safety_get_sensor(station, sensor_name, timeout=60, max_age=datetime.timedelta(minutes=10))
        print(f"{station}/{sensor_name}: {value=}")
