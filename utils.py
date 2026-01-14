import datetime
import inspect
import logging
import os
import random
import re
import string
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from contextlib import AbstractContextManager
from multiprocessing import shared_memory
from threading import Lock, Timer
from typing import Any, NamedTuple

import numpy as np
from astropy.coordinates import Angle
from astropy.units import deg, hourangle  # type: ignore

from common.filer import Filer
from common.paths import PathMaker

default_encoding = "utf-8"

logger = logging.getLogger("mast.unit." + __name__)


class RepeatTimer(Timer):
    def run(self):
        self.function(*self.args, **self.kwargs)
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


filer = Filer(logger)
path_maker = SingletonFactory.get_instance(PathMaker)


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
    if memory.buf is not None:
        bytes_array = bytearray(memory.buf)
    string_array = bytes_array.decode(encoding="utf-8")
    data = string_array[: string_array.find("\x00")]
    logger_.info(f"data: '{data}'")

    matches = re.findall(
        r"(\w+(?:\(\d+\))?)\s*=\s*(.*?)(?=(!|$|\w+(\(\d+\))?\s*=))", data
    )
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
        params.append(f"{k}={v}")
    data = " ".join(params)
    if memory.buf is not None:
        memory.buf[: memory.size] = bytearray(memory.size)  # wipe it clean
        memory.buf[: len(data)] = bytearray(data.encode(encoding="utf-8"))


def time_stamp():
    return isoformat_zulu(datetime.datetime.now())


def function_name():
    frame = inspect.currentframe()
    if frame is None or frame.f_back is None:
        return "UnknownFunction"
    try:
        caller_frame = frame.f_back
        func_name = caller_frame.f_code.co_name

        module = inspect.getmodule(caller_frame)
        class_name = None
        if 'self' in caller_frame.f_locals:
            class_name = caller_frame.f_locals['self'].__class__.__name__
        elif 'cls' in caller_frame.f_locals:
            class_name = caller_frame.f_locals['cls'].__name__
        ret = ''
        if module:
            ret += f"[{module.__name__}]."
        if class_name:
            ret += f"{class_name}."
        ret += func_name
        return ret
    finally:
        del frame  # avoid reference cycles


def caller_name() -> str:
    """
    Gets the name of the calling function's caller
    """
    current_frame = inspect.currentframe()
    if (
        current_frame is None
        or current_frame.f_back is None
        or current_frame.f_back.f_back is None
    ):
        return "UnknownCaller"

    return current_frame.f_back.f_back.f_code.co_name


def parse_coordinate(coord: float | str):
    return Angle(coord) if isinstance(coord, str) else coord


class Coord(NamedTuple):
    ra: Angle
    dec: Angle

    def __repr__(self):
        return (
            "["
            + f"{self.ra.to_string(unit=hourangle, decimal=True, precision=9)}, "
            + f"{self.dec.to_string(unit=deg, decimal=True, precision=9)}"
            + "]"
        )

    def __eq__(self, other):
        if not isinstance(other, Coord):
            return NotImplemented
        return np.isclose(
            self.ra.degree, other.ra.degree, atol=1e-6
        ) and np.isclose(  # type: ignore
            self.dec.degree, other.dec.degree, atol=1e-6
        )  # type: ignore


# class UnitRoi:
#     """
#     In unit terms a region-of-interest is centered on a pixel and has width and height
#     """

#     x: int
#     y: int
#     width: int
#     height: int

#     def __init__(self, _x: int, _y: int, width: int, height: int):
#         self.x = _x
#         self.y = _y
#         self.width = width
#         self.height = height

#     def to_imager_roi(self, binning: ImagerBinning | None = None) -> ImagerRoi:
#         """
#         An ASCOM camera ROI has a starting pixel (x, y) at lower left corner, width and height
#         Returns The corresponding camera region-of-interest
#         -------

#         """
#         if not binning:
#             binning = ImagerBinning(x=1, y=1)

#         return ImagerRoi(
#             x=(self.x - int(self.width / 2)) * binning.x,
#             y=(self.y - int(self.height / 2)) * binning.y,
#             width=self.width * binning.x,
#             height=self.height * binning.y,
#         )

#     @staticmethod
#     def from_dict(d):
#         return UnitRoi(d["sky_x"], d["sky_y"], d["width"], d["height"])

#     def __repr__(self) -> str:
#         return f"x={self.x},y={self.y},w={self.width},h={self.height}"


def cached(timeout_seconds):
    def decorator(func):
        cached_value: object | None = None  # Store the single cached value here
        cache_time = 0  # Store the time the cache was last updated

        def wrapper(*args, **kwargs):
            nonlocal cached_value, cache_time
            current_time = time.time()

            # If the cache is valid (not expired), return the cached value
            if cached_value is not None and (
                current_time - cache_time < timeout_seconds
            ):
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


def boxed_lines(lines: str | list[str], center: bool = False) -> list[str]:
    ret: list[str] = []
    max_len = 0

    if isinstance(lines, str):
        lines = [lines]

    for line in lines:
        if len(line) > max_len:
            max_len = len(line)
    if (max_len % 2) != 0:
        max_len += 1

    ret.append("+-" + "-" * max_len + "-+")
    for line in lines:
        if center:
            l_padding = " " * int((max_len - len(line)) / 2)
            r_padding = " " * (max_len - len(l_padding) - len(line))
        else:
            l_padding = ""
            r_padding = " " * (max_len - len(line))
        ret.append("| " + l_padding + line + r_padding + " |")
    ret.append("+-" + "-" * max_len + "-+")

    return ret


def generate_random_string(prefix="tmp_", length=15) -> str:
    # Calculate the number of random characters needed
    random_part_length = length - len(prefix)

    if random_part_length <= 0:
        raise ValueError("Length must be greater than the length of the prefix.")

    # Generate random characters
    random_part = "".join(
        random.choices(string.ascii_uppercase + string.digits, k=random_part_length)
    )

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
        args.append("-w")
    args.append(path)

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as ex:
        print(f"Error: {ex.stderr.strip()}")
        return None


def wslpath(path: str, to_windows: bool = False) -> str | None:
    """
    Converts a path from windows to cygwin (or the other way around)

    :param path: The path to convert
    :param to_windows:  from cygwin to windows
    :return:
    """
    args = ["wsl", "wslpath"]
    if to_windows:
        args.append("-w")
    args.append(path)

    try:
        result = subprocess.run(args, capture_output=True, text=True, check=True)
        return result.stdout.strip().replace(".localhost", r"$")
    except subprocess.CalledProcessError as ex:
        print(f"Error: {ex.stderr.strip()}")
        return None


def boxed_log(logger: logging.Logger, lines: str | list[str], center: bool = False, level=logging.INFO):
    if isinstance(lines, str):
        lines = [lines]
    for line in boxed_lines(lines, center):
        logger.log(level, line)

def canonic_unit_name(name: str) -> str | None:
    """
    Tries to make a canonic MAST unit name, accepting
    - mastw, mast00
    - mast1 to mast20 (with or w/out leading zero)

    :param name: The input name
    :return: canonic name ('mastw', 'mast00', 'mast01'..'mast20') or None
    """
    op = function_name()

    if not name:
        raise ValueError(f"{op}: Empty name")
    name = name.lower().strip()
    if name.startswith("mast"):
        suffix = name[4:]
        if suffix == "w" or suffix == "00":
            return name
        elif name.isdigit():
            unit_number = int(name[4:])
            if 1 >= unit_number <= 20:
                return name
            else:
                return None
    else:
        return None


class OperatingMode:
    """
    If a 'MAST_DEBUG' environment variable exists, we're operating
     in 'debug' mode, else in 'production' mode.
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def production_mode(cls):
        return not OperatingMode.debug_mode

    @classmethod
    def debug_mode(cls):
        return "MAST_DEBUG" in os.environ


class Timeout(AbstractContextManager):
    def __init__(self, sec: float):
        self.timeout = sec
        self.executor = ThreadPoolExecutor(max_workers=1)

    def run(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        future = self.executor.submit(func, *args, **kwargs)
        try:
            return future.result(timeout=self.timeout)
        except FuturesTimeout as exc:
            future.cancel()
            raise TimeoutError(
                f"Function call '{func.__name__}' exceeded timeout of {self.timeout:.2f} seconds"
            ) from exc

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.executor.shutdown(wait=False)


if __name__ == "__main__":
    from common.canonical import CanonicalResponse, CanonicalResponse_Ok

    try:
        x = 1 / 0
    except Exception as e:
        response = CanonicalResponse(errors=[str(e)])

    response = CanonicalResponse(errors=["err 1", "err 2"])
    response = CanonicalResponse(value={"tf": True, "val": 17})
    response = CanonicalResponse_Ok
    pass


def isoformat_zulu(dt: datetime.datetime) -> str:
    """
    Returns an ISO-8601 formatted string with a 'Z' suffix for UTC datetimes
    :param dt: The datetime to format
    :return: The ISO-8601 formatted string
    """
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    elif dt.tzinfo.utcoffset(dt) == datetime.timedelta(0):
        return dt.replace(tzinfo=None).isoformat() + "Z"
    else:
        return dt.isoformat()


def fromisoformat_zulu(s: str) -> datetime.datetime:
    """
    Parses an ISO-8601 formatted string with optional 'Z' suffix for UTC datetimes
    :param s: The ISO-8601 formatted string
    :return: The corresponding datetime
    """
    if s.endswith("Z"):
        s = s[:-1]
        return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.UTC)
    else:
        return datetime.datetime.fromisoformat(s)
