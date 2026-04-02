from datetime import date, datetime, timezone

import astropy.units as u
from astroplan import Observer
from astropy.coordinates import EarthLocation
from astropy.time import Time
from pydantic import BaseModel, ConfigDict, model_validator

from common.models.constraints import TimeWindow


class Building(BaseModel):
    names: list[str]
    unit_ids: str | list[str]
    units: list[str] | None = None
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_building(self):
        # self.unit_ids = normalize_unit_specifier(self.unit_ids)
        return self


class SunLimits(BaseModel):
    dusk: float = -18
    dawn: float = -18


class Location(BaseModel):
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    elevation: float | None = None
    sun_limits: SunLimits = SunLimits()


class Site(BaseModel):
    name: str
    project: str
    deployed_units: list[str] = []
    planned_units: list[str] = []
    units_in_maintenance: list[str] = []
    controller_host: str
    spec_host: str
    domain: str
    local: bool = False
    location: Location = Location()
    buildings: list[Building] = []
    unit_ids: str | list[str]

    def normalize_unit_specifier(self, spec) -> list[str]:
        """"""
        ret = []
        specs = []
        if isinstance(spec, list):
            specs = spec
        elif isinstance(spec, str) and "," in spec:
            specs = spec.split(",")
        elif isinstance(spec, str) and "-" in spec:
            low, high = spec.split("-")
            if low.isdigit() and high.isdigit():
                for i in range(int(low), int(high) + 1):
                    specs.append(str(i))
        else:
            specs = [spec]

        for specifier in specs:
            if isinstance(specifier, int):
                ret.append(f"{self.project}{specifier:02}")
            else:
                if not specifier.startswith(self.project):
                    ret.append(
                        f"{self.project}{int(specifier):02}"
                        if specifier.isdigit()
                        else f"{self.project}{specifier}"
                    )
                else:
                    ret.append(specifier)
        return ret

    @model_validator(mode="after")
    def validate_site(self):
        self.deployed_units = self.normalize_unit_specifier(self.deployed_units)
        self.planned_units = self.normalize_unit_specifier(self.planned_units)
        self.units_in_maintenance = self.normalize_unit_specifier(
            self.units_in_maintenance
        )
        self.unit_ids = self.normalize_unit_specifier(self.unit_ids)
        for building in self.buildings:
            building.units = self.normalize_unit_specifier(building.unit_ids)
        return self

        # "wheels": doc["wheels"],
        # "gratings": doc["gratings"],
        # "power_switch": doc["power_switch"],
        # "stage": doc["stage"],
        # "chiller": doc["chiller"],
        # "deepspec": doc["deepspec"],
        # "highspec": doc["highspec"],
        # "lamps": doc["lamps"],

    def observing_window(self, day: date | None = None) -> TimeWindow | None:
        if self.location.latitude is None or self.location.longitude is None:
            return None

        if day is None:
            day = date.today()

        observer = Observer(
            location=EarthLocation(
                lat=self.location.latitude * u.deg,  # type: ignore
                lon=self.location.longitude * u.deg,  # type: ignore
                height=(self.location.elevation or 0) * u.m,  # type: ignore
            )
        )

        noon = Time(
            datetime(day.year, day.month, day.day, 12, 0, 0, tzinfo=timezone.utc)
        )

        dusk = observer.sun_set_time(
            noon,
            which="next",
            horizon=self.location.sun_limits.dusk * u.deg,  # type: ignore
        )
        dawn = observer.sun_rise_time(
            dusk,
            which="next",
            horizon=self.location.sun_limits.dawn * u.deg,  # type: ignore
        )

        assert isinstance(dusk, Time) and isinstance(dawn, Time)
        window_start = dusk.to_datetime(timezone=timezone.utc)
        window_end = dawn.to_datetime(timezone=timezone.utc)

        assert isinstance(window_start, datetime) and isinstance(window_end, datetime)
        return TimeWindow(start=window_start, end=window_end)
