from pydantic import BaseModel, field_validator, model_validator
import logging
from common.config import Config
from common.mast_logging import init_log
from common.parsers import parse_unit_ids, parse_units
from common.spec import SpecGrating, BinningLiteral
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
    x_binning: BinningLiteral = 1
    y_binning: BinningLiteral = 1

    @field_validator('instrument')
    def validate_instrument(cls, instrument: str) -> str:
        return 'deepspec' if instrument in ['deep', 'deepspec'] else 'highspec'

class DeepSpecModel(SpectrographModel):
    pass

class HighSpecModel(SpectrographModel):
    grating: SpecGrating

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
    settings: SettingsModel
    spec: Union[DeepSpecModel, HighSpecModel]
    constraints: Optional[ConstraintsModel]

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
