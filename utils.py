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
import random
import string
import subprocess

import datetime
from typing import List, Any, Optional, Union, NamedTuple
from pydantic import BaseModel, field_validator
from enum import Enum, auto

import astropy.units as u

import time
from functools import cache

default_encoding = "utf-8"

BASE_SPEC_PATH = '/mast/api/v1/spec'
BASE_UNIT_PATH = '/mast/api/v1/unit'
BASE_CONTROL_PATH = '/mast/api/v1/control'

PLATE_SOLVING_SHM_NAME = 'PlateSolving_Image'

logger = logging.getLogger('mast.unit.' + __name__)


class OperatingMode(Enum):
    Day = auto(),
    Night = auto()


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
    """
    return inspect.currentframe().f_back.f_code.co_name


def caller_name():
    """
    Gets the name of the calling function's caller
    """
    return inspect.currentframe().f_back.f_back.f_code.co_name


def parse_coordinate(coord: float | str):
    return Angle(coord) if isinstance(coord, str) else coord


class CanonicalResponse(BaseModel):
    """
    Formalizes API responses.  An API method will return a CanonicalResponse, so that the
     API caller may safely parse it.

    An API method may ONLY return one of the following keys (in decreasing severity): 'errors' or 'value'

    - 'errors' - the method detected one or more errors (no 'value')
    - 'value' - all went well, this is the return value (may be 'None')
    """

    value: Optional[Any] = None
    errors: Optional[Union[List[str], str]] = None

    def __init__(self,
                 value: Optional[Any] = None,
                 errors: Optional[Union[List[str], str]] = None,
                 exception: Optional[Exception] | None = None):
        super().__init__()

        if exception:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            self.errors = traceback.format_exception(exc_type, exc_value, exc_traceback)
        elif errors:
            if isinstance(errors, str):
                self.errors = [errors]
            else:
                self.errors = errors
        else:
            self.value = value

    @property
    def is_error(self):
        return hasattr(self, 'errors') and self.errors is not None

    @property
    def succeeded(self):
        return self.errors is None

    @property
    def failed(self):
        return self.errors is not None

    @property
    def failure(self) -> List[str] | str | None:
        return self.errors


CanonicalResponse_Ok: CanonicalResponse = CanonicalResponse(value='ok')


class Coord(NamedTuple):
    ra: Angle
    dec: Angle

    def __repr__(self):
        return ("[" +
                f"{self.ra.to_string(u.hourangle, decimal=True, precision=9)}, " +
                f"{self.dec.to_string(u.deg, decimal=True, precision=9)}" +
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


def cached(timeout_seconds):
    def decorator(func):
        cached_value: object | None = None  # Store the single cached value here
        cache_time = 0  # Store the time the cache was last updated

        def wrapper(*args, **kwargs):
            nonlocal cached_value, cache_time
            current_time = time.time()

            # If the cache is valid (not expired), return the cached value
            if cached_value is not None and (current_time - cache_time < timeout_seconds):
                return cached_value

            # Otherwise, call the function and update the cache
            result = func(*args, **kwargs)
            cached_value = result
            cache_time = current_time
            return result

        # Add cache clearing function
        def clear_cache():
            nonlocal cached_value, cache_time

            cached_value = None
            cache_time = 0

        wrapper.clear_cache = clear_cache

        return wrapper
    return decorator


def boxed_lines(lines: str | List[str], center: bool = False) -> List[str]:
    ret: List[str] = []
    max_len = 0

    if isinstance(lines, str):
        lines = [lines]

    for l in lines:
        if len(l) > max_len:
            max_len = len(l)
    if (max_len % 2) != 0:
        max_len += 1

    ret.append('+-' + '-' * max_len + '-+')
    for line in lines:
        if center:
            l_padding = ' ' * int((max_len - len(line)) / 2)
            r_padding = ' ' * (max_len - len(l_padding) - len(lines))
        else:
            l_padding = ''
            r_padding = ' ' * (max_len - len(line))
        ret.append('| ' + l_padding + line + r_padding + ' |')
    ret.append('+-' + '-' * max_len + '-+')

    return ret


def generate_random_string(prefix="tmp_", length=15) -> str:
    # Calculate the number of random characters needed
    random_part_length = length - len(prefix)

    if random_part_length <= 0:
        raise ValueError("Length must be greater than the length of the prefix.")

    # Generate random characters
    random_part = ''.join(random.choices(string.ascii_letters + string.digits, k=random_part_length))

    # Combine prefix and random part
    return prefix + random_part.upper()


def cygpath(path: str, to_windows: bool = False) -> str | None:
    """
    Converts a path from windows to cygwin (or the other way around)

    :param path: The path to convert
    :param to_windows:  from cygwin to windows
    :return:
    """
    args = [r"\cygwin64\bin\cygpath"]
    if to_windows:
        args.append('-w')
    args.append(path)

    try:
        result = subprocess.run(args,
                                capture_output=True,
                                text=True,
                                check=True
                                )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"Error: {e.stderr.strip()}")
        return None


def wslpath(path: str, to_windows: bool = False) -> str | None:
    """
    Converts a path from windows to cygwin (or the other way around)

    :param path: The path to convert
    :param to_windows:  from cygwin to windows
    :return:
    """
    args = ['wsl', 'wslpath']
    if to_windows:
        args.append('-w')
    args.append(path)

    try:
        result = subprocess.run(args,
                                capture_output=True,
                                text=True,
                                check=True
                                )
        return result.stdout.strip().replace('.localhost', r'$')
    except subprocess.CalledProcessError as e:
        print(f"Error: {e.stderr.strip()}")
        return None

def canonic_unit_name(name: str) -> str | None:
    """
    Tries to make a canonic MAST unit name, accepting
    - mastw
    - mast1 to mast20 (with or w/out leading zero)

    :param name: The input name
    :return: canonic name ('mastw', 'mast01'..'mast20') or None
    """
    op = function_name()

    if not name:
        raise ValueError(f"{op}: Empty name")
    if name.startswith('mast'):
        suffix = name[4:]
        if suffix == 'w':
            return name
        elif name.isdigit():
            unit_number = int(name[4:])
            if 1 >= unit_number <= 20:
                return name
            else:
                return None
    else:
        return None
