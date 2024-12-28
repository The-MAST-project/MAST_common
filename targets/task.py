import logging

from datetime import datetime, timezone

from common.config import Config
from common.mast_logging import init_log
from typing import List, Optional, Union

from common.targets.model import TaskModel, TargetModel, ConstraintsModel, SpectrographModel, parse_units
from ulid import ULID
import socket

logger = logging.getLogger('targets')
init_log(logger)

from astropy.coordinates import Longitude, Latitude
from astropy import units as u

class Unit:
    def __init__(self, mnemonic: str):
        """
        Converts a unit mnemonic into a Unit object.
         NOTE: unit-ids are site-wise

        :param mnemonic: a '<site>:unit-id' string, e.g. 'wis:w' or 'ns:17'
        """
        self.hostname = None
        self.fqdn = None
        self.ipaddress = None
        cfg = Config()
        words = mnemonic.split(':')
        site = [s for s in cfg.sites if words[0] == s.name][0]
        uid = words[1]
        if uid.isdigit():
            uid = f"{int(uid):02}"
        self.hostname = f"{site.project}{uid}"
        self.fqdn = f"{self.hostname}.{site.domain}"
        try:
            self.ipaddress = socket.gethostbyname(self.fqdn)
        except socket.gaierror:
            self.ipaddress = None


class Target:

    def __init__(self, name: str, ra: float, dec: float, quorum: int, requested_units: List[str],
                 allocated_units: List[str], magnitude: float, magnitude_band: str):
        self.name = name
        self.ra: float = ra
        self.dec: float = dec
        self.quorum: int = quorum
        self.units: List[Unit] = []
        self.requested_units = requested_units
        self.allocated_units = allocated_units
        self.magnitude: float = magnitude
        self.magnitude_band: str = magnitude_band

        for requested_unit in requested_units:
            self.units.append(Unit(requested_unit))

def target_from_models(target_model: TargetModel) -> Target:

    if not target_model:
        raise ValueError('No targets specified, at least one expected')

    try:
        ra: Longitude = Longitude(target_model.ra, unit=u.hourangle)
    except ValueError as e:
        raise ValueError(f"Invalid RA '{target_model.ra}' (error={e})")

    try:
        dec: Latitude = Latitude(target_model.dec, unit=u.deg)
    except ValueError as e:
        raise ValueError(f"Invalid DEC '{target_model.dec}' (error={e})")

    success, value = parse_units(target_model.requested_units)
    if success:
        units = value
    else:
        raise ValueError(f"Invalid units specifier '{target_model.requested_units}', error: {value}")

    return Target(name=target_model.name,
                  ra=ra.value,
                  dec=dec.value,
                  quorum=target_model.quorum,
                  requested_units=target_model.requested_units,
                  allocated_units=target_model.allocated_units,
                  magnitude=target_model.magnitude,
                  magnitude_band=target_model.magnitude_band)

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
    def __init__(self, constraints_model: ConstraintsModel):
        self.moon = Moon(constraints_model.moon.max_phase, constraints_model.moon.min_distance) if hasattr(constraints_model, 'airmass') else None
        self.airmass = Airmass(constraints_model.airmass.max) if hasattr(constraints_model, 'airmass') else None
        self.seeing = Seeing(constraints_model.seeing.max) if hasattr(constraints_model, 'seeing') else None
        self.time_window = TimeWindow(constraints_model.when.start, constraints_model.when.end) if hasattr(constraints_model, 'when') else None

class Spectrograph:
    def __init__(self, spec_model: SpectrographModel):
        local_site: Site | None = None
        sites = Config().get_sites()
        result = [s for s in sites if hasattr(s, 'local') and s.local == True]
        if result:
            site = result[0]

        self.hostname = f"{local_site.project}-{local_site.name}-spec"
        self.fqdn = f"{self.hostname}.{site.domain}"
        try:
            self.ipaddress = socket.gethostbyname(self.fqdn)
        except socket.gaierror:
            self.ipaddress = None
        self.instrument: str = spec_model.instrument
        self.exposure: float = spec_model.exposure
        self.lamp = spec_model.lamp
        self.binning_x = spec_model.binning_x
        self.binning_y = spec_model.binning_y

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


class Task:

    def __init__(self, task_model: TaskModel, toml, file_name: Optional[str] = None):
        self.name = task_model.settings.name
        self.toml = toml
        self.file_name: str = file_name
        self.model = task_model
        self.settings = Settings(
            ulid=task_model.settings.ulid if task_model.settings.ulid else ULID(),
            owner=task_model.settings.owner if task_model.settings.owner else None,
            merit=task_model.settings.merit if task_model.settings.merit else 0,
            state=task_model.settings.state if task_model.settings.state else 'new',
            timeout=task_model.settings.timeout_to_guiding if task_model.settings.timeout_to_guiding else 0,
        )
        self.target: Target = target_from_models(task_model.target)
        self.spec = Spectrograph(task_model.spec)
        self.constraints = Constraints(task_model.constraints)
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
        #  - if there are viable targets, tell the spec to expose
        #
        self.add_event('run')

    def abort(self):
        """
        Aborts the task
        :return:
        """
        self.add_event('abort')
        for unit in self.target.units:
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
    # file = 'dummy-target.toml'
    # task = None
    # try:
    #     with open(file, 'r') as fp:
    #         task = targets.load(fp)
    # except ValueError as e:
    #     raise ValueError(f"{e}")

    # print(json.dumps(targets.new(), indent=2))

    doc = """
    [section]
        field = 
    """

    import tomlkit
    t = tomlkit.loads(doc)
