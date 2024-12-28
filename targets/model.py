from pydantic import BaseModel, field_validator, model_validator
import logging
from common.config import Config
from common.mast_logging import init_log
from common.parsers import parse_unit_ids
from typing import Literal, List, Optional, Union
import re

from astropy.coordinates import Longitude, Latitude
from astropy import units as u

logger = logging.getLogger('targets')
init_log(logger)
class CalibrationLampModel(BaseModel):
    on: bool
    filter: str

    @field_validator('filter')
    def validate_filter(cls, filter_name: str) -> str | None:
        thar_filters = Config().get_specs()['wheels']['ThAr']['filters']

        if filter_name not in thar_filters.values():
            raise ValueError \
                (f"Invalid filter '{filter_name}', currently mounted ThAr filters are: {[f"{k}:{v}" for k, v in thar_filters.items() if v]}")
        return filter_name

class SpectrographModel(BaseModel):
    exposure: float
    lamp: CalibrationLampModel
    instrument: Literal['deep', 'deepspec', 'high', 'highspec']
    binning_x: Literal[1, 2, 4] = 1
    binning_y: Literal[1, 2, 4] = 1

    @field_validator('instrument')
    def validate_instrument(cls, instrument: str) -> str:
        return 'deepspec' if instrument in ['deep', 'deepspec'] else 'highspec'

class DeepSpecModel(SpectrographModel):
    binning: Literal[1, 2, 4] = 1

class HighSpecModel(SpectrographModel):
    gratings: Union[str, List[str]]

    @field_validator('gratings')
    def validate_gratings(cls, specs: Union[str, List[str]]):
        valid_gratings_dict = Config().get_specs()['gratings']
        valid_grating_names = list(valid_gratings_dict.keys())
        ret: List[str] = []

        if isinstance(specs, str):
            specs = [specs]
        for spec in specs:
            spec = spec.lower()
            if spec not in valid_grating_names:
                raise ValueError(f"Invalid grating specifier '{spec}', must be one of {valid_grating_names}.")
            else:
                ret.append(spec)
        return ret

class TargetModel(BaseModel):
    name: str
    ra: Union[float, str, None] = None
    dec: Union[float, str, None] = None
    requested_units: Union[str, List[str], None] = None
    allocated_units: Union[str, List[str], None] = None
    quorum: Optional[int] = 1
    exposure: Optional[float] = 5 * 60
    priority: Optional[Literal['lowest', 'low', 'normal', 'high', 'highest', 'too']] = 'normal'
    magnitude: Optional[float]
    magnitude_band: Optional[str]

    @model_validator(mode='after')
    def validate_target(cls, values):
        quorum = values.quorum
        units = values.requested_units
        if len(units) < quorum:
            raise ValueError(f"Expected {quorum=}, got only {len(units)} ({units})")
        return values

    @field_validator('requested_units', mode='before')
    def validate_input_units(cls, specifiers: Union[str, List[str]]) -> List[str]:
        success, value = parse_units(specifiers if isinstance(specifiers, list) else [specifiers])
        if success:
            return value
        else:
            raise ValueError(f"Invalid units specifier '{specifiers}', errors: {value}")

    @field_validator('ra', mode='before')
    def validate_ra(cls, value):
        return Longitude(value, unit=u.hourangle).value if value is not None else None

    @field_validator('dec', mode='before')
    def validate_dec(cls, value):
        return Latitude(value, unit=u.deg).value if value is not None else None

class SettingsModel(BaseModel):
    name: Union[str, None] = None
    ulid: Union[str, None] = None
    owner: Optional[str] = None
    merit: Union[int, None] = None
    state: Literal['new', 'in-progress', 'postponed', 'canceled', 'completed'] = 'new'
    timeout_to_guiding: Union[int, None] = None

    @field_validator('owner')
    def validate_owner(cls, user: str) -> str:
        valid_users = Config().get_users()
        if user not in valid_users:
            raise ValueError(f"Invalid user '{user}'")
        user = Config().get_user(user)
        if not 'canOwnTasks' in user['capabilities']:
            raise ValueError(f"User '{user['name']}' cannot own targets")
        return user['name']

class MoonConstraintModel(BaseModel):
    max_phase: float
    min_distance: float

class AirmassConstraintModel(BaseModel):
    max: float

class SeeingConstraintModel(BaseModel):
    max: float

class ConstraintsModel(BaseModel):
    moon: Optional[MoonConstraintModel]
    airmass: Optional[AirmassConstraintModel]
    seeing: Optional[SeeingConstraintModel]

class TaskModel(BaseModel):
    settings: SettingsModel
    target: TargetModel
    spec: Union[DeepSpecModel, HighSpecModel]
    constraints: Optional[ConstraintsModel]

def parse_units(specs: List[str]) -> tuple:
    """
    Parses and validates unit specifiers.  Valid specifiers:
     - 'w', 'wis::w', 'ns:north:9', 'ns:17'

    :param specs: one or more unit specifiers
    :return: on success True, values, on failure False, List[errors], where values is a list of 'site:building:unit-id' triplets
    """
    errors: List[str] = []
    ret: List[str] = []
    sites_cfg = Config().get_sites()
    if isinstance(specs, str):
        specs = [specs]

    for spec in specs:
        building = None
        building_name = None

        match = re.match(r'^(?:(?P<site>\w+):)?(?:(?P<building>\w+):)?(?P<units>[,a-zA-Z0-9_-]+)$', spec)
        if match:
            site_name = match.group(1)
            building_name = match.group(2)
            units_spec = match.group(3)
        else:
            match = re.match(r'^(?:(?P<site>\w+):{1,2})?(?P<units>\w+)$', spec)
            if match:
                site_name = match.group('site')
                units_spec = match.group('units')
            else:
                errors.append(f"Invalid units spec: {spec}")
                continue

        site = None
        if site_name:
            site = [s for s in sites_cfg if s.name == site_name][0]
            if not site:
                errors.append(f"Invalid site: '{site_name}'")
                continue
        else:
            result = [s for s in sites_cfg if hasattr(s, 'local') and s.local == True]
            if result:
                site = result[0]

        if building_name:
            if building_name.isdigit() and int(building_name) in range(0, len(site.buildings)):
                building = site.buildings[int(building_name)]
            else:
                for b in site.buildings:
                    if building_name in b.names:
                        building = b
                        break
            if not building:
                # a building was specified but it's not valid
                errors.append(f"Invalid building: '{building_name}' at site '{site.name}'")
                continue

        units = parse_unit_ids(units_spec)
        for unit in units:
            if building and unit in building.units:
                unit_in_site = site.units_map[f"{building.id}:{unit}"]
                ret.append(f"{site.name}:{unit_in_site}")
            elif unit in site.units:
                ret.append(f"{site.name}:{unit}")
            else:
                errors.append(f"Invalid unit: '{unit}' ({spec=})")

    return (False, errors) if errors else (True, ret)
