from pydantic import BaseModel, ConfigDict, model_validator


class Building(BaseModel):
    names: list[str]
    unit_ids: str | list[str]
    units: list[str] | None = None
    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_building(self):
        # self.unit_ids = normalize_unit_specifier(self.unit_ids)
        return self


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
    location: str = "Unknown"
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


