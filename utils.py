from abc import abstractmethod
from threading import Timer, Lock
import logging
from astropy.coordinates import Angle
import inspect
from multiprocessing import shared_memory
import re
from abc import ABC
import sys
import traceback
from common.activities import Activities
from common.filer import Filer
from common.paths import PathMaker
from common.camera import CameraBinning, CameraRoi

import datetime
from typing import List, Any, Optional, Union, NamedTuple
from pydantic import BaseModel, field_validator

import astropy.units as u

default_encoding = "utf-8"

BASE_SPEC_PATH = '/mast/api/v1/spec'
BASE_UNIT_PATH = '/mast/api/v1/unit'
BASE_CONTROL_PATH = '/mast/api/v1/control'

logger = logging.getLogger('mast.unit.' + __name__)


class RepeatTimer(Timer):
    def run(self):
        while not self.finished.wait(self.interval):
            self.function(*self.args, **self.kwargs)


class SingletonFactory:
    _instances = {}
    _lock = Lock()

    @staticmethod
    def get_instance(class_type):
        with SingletonFactory._lock:
            if class_type not in SingletonFactory._instances:
                SingletonFactory._instances[class_type] = class_type()
        return SingletonFactory._instances[class_type]


filer = Filer()
path_maker = SingletonFactory.get_instance(PathMaker)


def deep_dict_update(original: dict, update: dict):
    """
    Recursively update a dictionary with nested dictionaries.
    :param original: The original dictionary to be updated, in place.
    :param update: The dictionary with updates.
    """
    for key, value in update.items():
        if isinstance(value, dict) and key in original:
            # If the value is a dict and the key exists in the original dict,
            # perform a deep update
            deep_dict_update(original[key], value)
        else:
            # Otherwise, update or add the key-value pair to the original dict
            original[key] = value


def deep_dict_difference(old: dict, new: dict):
    if isinstance(old, dict) and isinstance(new, dict):
        difference = {}
        all_keys = set(old.keys()).union(new.keys())
        for key in all_keys:
            if key in old and key in new:
                diff = deep_dict_difference(old[key], new[key])
                if diff is not None:
                    difference[key] = diff
            elif key in new:
                difference[key] = new[key]
            elif key in old:
                difference[key] = old[key]
        return difference if difference else None
    elif isinstance(old, list) and isinstance(new, list):
        length = max(len(old), len(new))
        difference = []
        for i in range(length):
            old_val = old[i] if i < len(old) else None
            new_val = new[i] if i < len(new) else old_val
            diff = deep_dict_difference(old_val, new_val)
            difference.append(diff)
        return difference if any(item is not None for item in difference) else None
    else:
        return new if old != new else None


def deep_dict_is_empty(d):
    if not isinstance(d, (dict, list)):
        return False  # Not a dictionary or list

    if not d:
        return True  # Dictionary or list is empty

    if isinstance(d, list):
        return all(deep_dict_is_empty(item) for item in d)

    for value in d.values():
        if isinstance(value, (dict, list)):
            if not deep_dict_is_empty(value):
                return False  # Nested dictionary or list is not empty
        elif value:
            return False  # Non-empty value found

    return True  # All nested dictionaries and lists are empty


class Component(ABC, Activities):

    @abstractmethod
    def startup(self):
        """
        Called whenever an observing session starts (at sun-down or when safety returns)
        :return:
        """
        pass

    @abstractmethod
    def shutdown(self):
        """
        Called whenever an observing session is terminated (at sun-up or when becoming unsafe)
        :return:
        """
        pass

    @abstractmethod
    def abort(self):
        """
        Immediately terminates any in-progress activities and returns the component to its
         default state.
        :return:
        """
        pass

    @abstractmethod
    def status(self):
        """
        Returns the component's current status
        :return:
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """The getter method for the abstract name property."""
        pass

    @name.setter
    @abstractmethod
    def name(self, value: str):
        """The setter method for the abstract name property."""
        pass

    @property
    @abstractmethod
    def operational(self) -> bool:
        """The getter method for the abstract name property."""
        pass

    @operational.setter
    @abstractmethod
    def operational(self, value: str) -> bool:
        """The setter method for the abstract name property."""
        pass

    @property
    @abstractmethod
    def why_not_operational(self) -> List[str]:
        pass

    @property
    @abstractmethod
    def detected(self) -> bool:
        pass

    @property
    @abstractmethod
    def connected(self) -> bool:
        pass

    @property
    @abstractmethod
    def was_shut_down(self) -> bool:
        pass

    def component_status(self) -> dict:
        return {
            'detected': self.detected,
            'connected': self.connected,
            'activities': self.activities,
            'activities_verbal': self.activities.__repr__(),
            'operational': self.operational,
            'why_not_operational': self.why_not_operational,
            'was_shut_down': self.was_shut_down,
        }


def quote(s: str):
    # return 'abc'
    return "'" + s.replace("'", "\\'") + "'"


class HelpResponse:
    method: str
    description: str

    def __init__(self, method: str, doc: str):
        self.method = method
        self.description = doc


class Subsystem:
    path: str
    obj: object
    obj_name: str
    method_objects: list[object]
    method_names: list[str]
    method_docs: list[str]

    def __init__(self, path: str, obj: object, obj_name: str):
        self.path = path
        self.obj = obj
        self.obj_name = obj_name


def parse_params(memory: shared_memory.SharedMemory, logger_: logging.Logger) -> dict:
    bytes_array = bytearray(memory.buf)
    string_array = bytes_array.decode(encoding='utf-8')
    data = string_array[:string_array.find('\x00')]
    logger_.info(f"data: '{data}'")

    matches = re.findall(r'(\w+(?:\(\d+\))?)\s*=\s*(.*?)(?=(!|$|\w+(\(\d+\))?\s*=))', data)
    d = {}
    for match in matches:
        key = match[0]
        value = match[1].strip()
        logger_.info(f"key={match[0]}, value='{value}'")
        d[key] = value
    return d


def store_params(memory: shared_memory.SharedMemory, d: dict):
    params = []
    for k, v in d.items():
        params.append(f'{k}={v}')
    data = ' '.join(params)
    memory.buf[:memory.size] = bytearray(memory.size)  # wipe it clean
    memory.buf[:len(data)] = bytearray(data.encode(encoding='utf-8'))


def time_stamp(d: dict):
    d['time_stamp'] = datetime.datetime.now().isoformat()


def function_name():
    """
    Gets the name of the calling function from the stack
    Returns
    -------

    """
    return inspect.currentframe().f_back.f_code.co_name


def parse_coordinate(coord: float | str):
    return Angle(coord) if isinstance(coord, str) else coord


class CanonicalResponse(BaseModel):
    """
    Formalizes API responses.  An API method will return a CanonicalResponse, so that the
     API caller may safely parse it.

    An API method may ONLY return one of the following keys (in decreasing severity): 'exception', 'errors' or 'value'

    - 'exception' - an exception occurred, delivers the details (no 'value')
    - 'errors' - the method detected one or more errors (no 'value')
    - 'value' - all went well, this is the return value (maybe 'None')
    """

    value: Optional[Any] = None
    errors: Optional[Union[List[str], str]] = None
    exception: Optional[List[str]] = None

    # def __init__(self,
    #              value: Any = None,
    #              errors: List[str] | str | None = None,
    #              exception: Exception | None = None
    #              ):
    #     super.__init__()
    #
    #     if exception:
    #         exc_type, exc_value, exc_traceback = sys.exc_info()
    #         traceback_string = traceback.format_exception(exc_type, exc_value, exc_traceback)
    #         self.exception = traceback_string
    #     elif errors:
    #         self.errors = errors
    #     else:
    #         self.value = value

    @field_validator('exception', mode='before')
    def process_exception(cls, v):
        if v:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            return ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        return v

    @field_validator('errors', mode='before')
    def ensure_errors_list(cls, v):
        if isinstance(v, str):
            return [v]
        return v

    @property
    def is_error(self):
        return hasattr(self, 'errors') and self.errors is not None

    @property
    def is_exception(self):
        return hasattr(self, 'exception') and self.exception is not None

    @property
    def succeeded(self):
        return hasattr(self, 'value')  # and self.value is not None

    @property
    def failed(self):
        if self.is_exception:
            return self.exception
        if self.is_error:
            return self.errors

    @property
    def failure(self) -> List[str] | str | None:
        if not self.failed:
            return None
        if self.is_exception:
            return self.exception
        if self.is_error:
            return self.errors if self.errors is not None else None

    @classmethod
    @property
    def ok(cls):
        return cls(value='ok')


class Coord(NamedTuple):
    ra: Angle
    dec: Angle

    def __repr__(self):
        return ("[" +
                f"{self.ra.to_string(u.hourangle, decimal=True, precision=3)}, " +
                f"{self.dec.to_string(u.deg, decimal=True, precision=3)}" +
                "]")


class UnitRoi:
    """
    In unit terms a region-of-interest is centered on a pixel and has width and height
    """
    fiber_x: int
    fiber_y: int
    width: int
    height: int

    def __init__(self, fiber_x: int, fiber_y: int, width: int, height: int):
        self.fiber_x = fiber_x
        self.fiber_y = fiber_y
        self.width = width
        self.height = height

    def to_camera_roi(self, binning: CameraBinning = CameraBinning(1, 1)) -> CameraRoi:
        """
        An ASCOM camera ROI has a starting pixel (x, y) at lower left corner, width and height
        Returns The corresponding camera region-of-interest
        -------

        """
        return CameraRoi(
            (self.fiber_x - int(self.width / 2)) * binning.x,
            (self.fiber_y - int(self.height / 2)) * binning.y,
            self.width * binning.x,
            self.height * binning.y
        )

    @staticmethod
    def from_dict(d):
        return UnitRoi(d['fiber_x'], d['fiber_y'], d['width'], d['height'])

    def __repr__(self) -> str:
        return f"x={self.fiber_x},y={self.fiber_y},w={self.width},h={self.height}"


CanonicalResponse_Ok: CanonicalResponse = CanonicalResponse(value='ok')
