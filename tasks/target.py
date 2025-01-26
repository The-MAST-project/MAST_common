import logging
from datetime import datetime, timezone
import ulid
from common.config import Config
from common.mast_logging import init_log
from common.models.assignments import RemoteAssignment
from typing import List, Optional, Union
import json
import common.tasks

from common.tasks.models import TargetModel, SpecificationModel, ConstraintsModel, SpectrographAssignment, parse_units
import socket

logger = logging.getLogger('tasks')
init_log(logger)

from astropy.coordinates import Longitude, Latitude
from astropy import units as u

class Specification:

    def __init__(self, model: SpecificationModel):
        self.name = model.name
        self.ra: float =  model.ra
        self.dec: float =  model.dec
        self.quorum: int = model. quorum
        self.units: List[RemoteAssignment] = []
        self.requested_units =  model.requested_units if isinstance( model.requested_units, list) \
            else [ model.requested_units]
        self.allocated_units =  model.allocated_units if isinstance( model.allocated_units, list) \
            else [ model.allocated_units]

        self.magnitude: float =  model.magnitude
        self.magnitude_band: str =  model.magnitude_band

        for requested_unit in  model.requested_units:
            self.units.append(RemoteAssignment(requested_unit))

        try:
            ra: Longitude = Longitude(model.ra, unit=u.hourangle)
        except ValueError as e:
            raise ValueError(f"Invalid RA '{model.ra}' (error={e})")

        try:
            dec: Latitude = Latitude(model.dec, unit=u.deg)
        except ValueError as e:
            raise ValueError(f"Invalid DEC '{model.dec}' (error={e})")

        success, value = parse_units(model.requested_units)
        if success:
            units = value
        else:
            raise ValueError(f"Invalid units specifier '{model.requested_units}', error: {value}")

class Moon:
    def __init__(self, max_phase: float, min_distance: float):
        self.max_phase: float = max_phase
        self.min_distance: float = min_distance

class Airmass:
    def __init__(self, max_airmass: float):
        self.max: float = max_airmass

class Seeing:
    def __init__(self, max_seeing: float):
        self.max: float = max_seeing

class TimeWindow:
    def __init__(self, start: str | None = None, end: str | None = None):
        self.start: str = start
        self.end: str = end

class Constraints:
    def __init__(self, model: ConstraintsModel):
        self.moon = Moon(model.moon.max_phase, model.moon.min_distance) if hasattr(model, 'moon') else None
        self.airmass = Airmass(model.airmass.max) if hasattr(model, 'airmass') else None
        self.seeing = Seeing(model.seeing.max) if hasattr(model, 'seeing') else None
        self.time_window = TimeWindow(model.when.start, model.when.end) if hasattr(model, 'when') else None

class Spectrograph:
    def __init__(self, model: SpectrographAssignment):
        local_site = None
        sites = Config().get_sites()
        result = {k: v for k, v in sites.items() if 'local' in sites[k] and sites[k]['local'] == True}
        if result:
            local_site = result

        self.hostname = f"{local_site['project']}-{local_site['name']}-spec"
        self.fqdn = f"{self.hostname}.{local_site['domain']}"
        try:
            self.ipaddress = socket.gethostbyname(self.fqdn)
        except socket.gaierror:
            self.ipaddress = None
        self.instrument: str = model.instrument
        self.exposure: float = model.exposure
        self.lamp = model.lamp
        self.binning_x = model.x_binning
        self.binning_y = model.y_binning

class Settings:
    def __init__(self, ulid: str, owner: str, merit: int, timeout: float, state: str):
        self.ulid: Optional[str] = ulid
        self.owner: Union[str, None] = owner
        self.merit: Optional[int] = merit
        self.timeout: Optional[float] = timeout
        self.state: Optional[str] = state

class Event:
    def __init__(self, desc: str):
        self.desc: str = desc
        self.date: str = datetime.now(timezone.utc).isoformat()


class Target:

    def __init__(self, model: TargetModel, toml, file_name: Optional[str] = None):
        self.name = model.name
        self.toml = toml
        self.file_name: str = file_name
        self.model = model
        task_ulid = model.settings.ulid if model.settings.ulid else str(ulid.new()).lower()
        self.settings = Settings(
            ulid=task_ulid,
            owner=model.settings.owner if model.settings.owner else None,
            merit=model.settings.merit if model.settings.merit else 0,
            state=model.settings.state if model.settings.state else 'new',
            timeout=model.settings.timeout_to_guiding if model.settings.timeout_to_guiding else 0,
        )
        self.specification: Specification(model.specification)
        self.spectrograph = Spectrograph(model.spectrograph)
        self.constraints = Constraints(model.constraints)
        self.events = []

    def add_event(self, desc: str):
        self.events.append(Event(desc))

    def save(self):
        """
        Uses a task's TOML field to save the task
        :return:
        """
        pass

    def complete(self):
        """
        Sets the task's state to 'complete' and moves the file to the 'completed' folder
        :return:
        """
        self.add_event('completed')

    def run(self):
        """
        Runs the task
        :return:
        """
        # TODO:
        #  - if the spec is not operational, return, else tell the spec to prepare (lamp, filter, etc)
        #  - for each target
        #    - let all the target's units acquire and reach UnitActivities.Guiding within timeout
        #    - if the minimum required units are guiding, the target is viable, else tell the units to abort
        #  - if there are viable tasks, tell the spec to expose
        #
        self.add_event('run')

    def abort(self):
        """
        Aborts the task
        :return:
        """
        self.add_event('abort')
        for unit in self.specification.units:
            pass  # unitApi(u, 'abort')

    def to_dict(self):
        # Convert the User object to a dictionary and recursively convert nested objects
        def convert(obj):
            if obj == self.model or obj == self.toml:
                return
            if isinstance(obj, list):
                return [convert(item) for item in obj]
            elif hasattr(obj, "__dict__"):
                return {key: convert(value) for key, value in obj.__dict__.items()}
            elif obj == self.settings.ulid:
                return f"{self.settings.ulid}"
            else:
                return obj

        return convert(self)


if __name__ == '__main__':
    file = 'dummy-target.toml'

    with open(file, 'r') as fp:
        target = common.targets.load(fp)
    print(json.dumps(target, indent=2))

