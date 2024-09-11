from abc import abstractmethod
from enum import IntFlag
from threading import Timer, Lock, Thread
import logging
import platform
import os
import io
from astropy.coordinates import Angle
import inspect
from multiprocessing import shared_memory
import re
from abc import ABC
import sys
import traceback
import socket
import win32api

import datetime
from typing import List, Any, Optional, Union, NamedTuple
import shutil
from pydantic import BaseModel, field_validator

import astropy.units as u

default_log_level = logging.DEBUG
default_encoding = "utf-8"

BASE_SPEC_PATH = '/mast/api/v1/spec'
BASE_UNIT_PATH = '/mast/api/v1/unit'
BASE_CONTROL_PATH = '/mast/api/v1/control'

logger = logging.getLogger('mast.unit.utils')


class Timing:
    start_time: datetime.datetime
    end_time: datetime.datetime
    duration: datetime.timedelta

    def __init__(self):
        self.start_time = datetime.datetime.now()

    def end(self):
        self.end_time = datetime.datetime.now()
        self.duration = self.end_time - self.start_time


class Activities:

    Idle: IntFlag = 0

    def __init__(self):
        self.activities: IntFlag = Activities.Idle
        self.timings = dict()

    def start_activity(self, activity: IntFlag):
        self.activities |= activity
        self.timings[activity] = Timing()
        logger.info(f"started activity {activity.__repr__()}")

    def end_activity(self, activity: IntFlag):
        if not self.is_active(activity):
            return
        self.activities &= ~activity
        self.timings[activity].end()
        logger.info(f"ended activity {activity.__repr__()}, duration={self.timings[activity].duration}")

    def is_active(self, activity):
        return (self.activities & activity) != 0

    def is_idle(self):
        return self.activities == 0

    def __repr__(self):
        return self.activities.__repr__()


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


class DailyFileHandler(logging.FileHandler):

    filename: str = ''
    path: str

    def make_file_name(self):
        """
        Produces file names for the DailyFileHandler, which rotates them daily at noon (UT).
        The filename has the format <top><daily><bottom> and includes:
        * A top section (either /var/log/mast on Linux or %LOCALAPPDATA%/mast on Windows
        * The daily section (current date as %Y-%m-%d)
        * The bottom path, supplied by the user
        Examples:
        * /var/log/mast/2022-02-17/server/app.log
        * c:\\User\\User\\LocalAppData\\mast\\2022-02-17\\main.log
        :return:
        """
        top = ''
        if platform.platform() == 'Linux':
            top = '/var/log/mast'
        elif platform.platform().startswith('Windows'):
            top = os.path.join(os.path.expandvars('%LOCALAPPDATA%'), 'mast')
        now = datetime.datetime.now()
        if now.hour < 12:
            now = now - datetime.timedelta(days=1)
        return os.path.join(top, f'{now:%Y-%m-%d}', self.path)

    def emit(self, record: logging.LogRecord):
        """
        Overrides the logging.FileHandler's emit method.  It is called every time a log record is to be emitted.
        This function checks whether the handler's filename includes the current date segment.
        If not:
        * A new file name is produced
        * The handler's stream is closed
        * A new stream is opened for the new file
        The record is emitted.
        :param record:
        :return:
        """
        filename = self.make_file_name()
        if not filename == self.filename:
            if self.stream is not None:
                # we have an open file handle, clean it up
                self.stream.flush()
                self.stream.close()
                self.stream = None  # See Issue #21742: _open () might fail.

            self.baseFilename = filename
            os.makedirs(os.path.dirname(self.baseFilename), exist_ok=True)
            self.stream = self._open()
        logging.StreamHandler.emit(self, record=record)

    def __init__(self, path: str, mode='a', encoding=None, delay=True, errors=None):
        self.path = path
        if "b" not in mode:
            encoding = io.text_encoding(encoding)
        logging.FileHandler.__init__(self, filename='', delay=delay, mode=mode, encoding=encoding, errors=errors)


def is_windows_drive_mapped(drive_letter):
    try:
        drives = win32api.GetLogicalDriveStrings()
        drives = drives.split('\000')[:-1]
        return drive_letter.upper() + "\\" in drives
    except Exception as e:
        print(f"An error occurred: {e}")
        return False


class Location:
    def __init__(self, drive: str, prefix: str):
        self.drive = drive
        self.prefix = prefix
        self.root = self.drive + self.prefix


class Filer:
    def __init__(self):
        self.local = Location('C:\\', 'MAST\\')
        self.shared = Location('Z:\\', f"MAST\\{socket.gethostname()}\\") if is_windows_drive_mapped('Z:') \
            else Location('C:\\', 'MAST\\')
        self.ram = Location('D:\\', 'MAST\\') if is_windows_drive_mapped('D:') \
            else Location('C:\\', 'MAST\\')

    @staticmethod
    def copy(src: str, dst: str):
        try:
            shutil.copy2(src, dst)
            os.unlink(src)
            logger.info(f"moved '{src}' to '{dst}'")
        except Exception as e:
            logger.exception(f"failed to move '{src} to '{dst}'", exc_info=e)

    def move_ram_to_shared(self, files: str | List[str]):
        if isinstance(files, str):
            files = [files]

        for file in files:
            src = file
            dst = file.replace(self.ram.root, self.shared.root)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            Thread(name='ram-to-shared-mover', target=self.copy, args=[src, dst]).start()


filer = Filer()


class PathMaker:

    @staticmethod
    def make_seq(folder: str, camera: str | None = None) -> str:
        """
        Creates a sequence number by maintaining a '.seq' file.
        The sequence may be camera specific or camera agnostic.
        :param folder: Where to maintain the '.seq' file
        :param camera: What camera is the sequence for
        :return: The resulting sequence string
        """
        if camera:
            seq_file = os.path.join(folder, f'.{camera}.seq.txt')
        else:
            seq_file = os.path.join(folder, '.seq.txt')

        os.makedirs(os.path.dirname(seq_file), exist_ok=True)
        if os.path.exists(seq_file):
            with open(seq_file) as f:
                seq = int(f.readline())
        else:
            seq = 0
        seq += 1
        with open(seq_file, 'w') as file:
            file.write(f'{seq}\n')

        return f"{seq:04d}"

    @staticmethod
    def make_daily_folder_name(root: str | None = None):
        if not root:
            root = filer.ram.root
        d = os.path.join(root, datetime.datetime.now().strftime('%Y-%m-%d'))
        os.makedirs(d, exist_ok=True)
        return d

    def make_exposures_folder(self, root: str | None = None) -> str:
        folder = os.path.join(self.make_daily_folder_name(root=root), 'Exposures')
        os.makedirs(folder, exist_ok=True)
        return folder

    def make_autofocus_folder(self, root: str | None = None) -> str:
        autofocus_folder = os.path.join(self.make_daily_folder_name(root=root), 'Autofocus')
        ret: str = os.path.join(autofocus_folder, self.make_seq(autofocus_folder))
        os.makedirs(ret, exist_ok=True)
        return ret

    def make_acquisition_folder(self, tags: dict | None = None) -> str:
        acquisitions_folder = os.path.join(self.make_daily_folder_name(), 'Acquisitions')
        os.makedirs(acquisitions_folder, exist_ok=True)
        parts: List[str] = [
            f"seq={PathMaker.make_seq(folder=acquisitions_folder)}",
            f"time={self.current_utc()}"
        ]
        if tags:
            for k, v in tags.items():
                parts.append(f"{k}={v}" if v else "{k}")

        folder = os.path.join(acquisitions_folder, ','.join(parts))
        os.makedirs(folder, exist_ok=True)
        return folder

    def make_guidings_folder(self, root: str | None = None, base_folder: str | None = None):
        if base_folder is not None:
            guiding_folder = os.path.join(base_folder, 'Guidings')
        else:
            if not root:
                root = filer.ram.root
            guiding_folder = os.path.join(self.make_daily_folder_name(root=root), 'Guidings')

        os.makedirs(guiding_folder, exist_ok=True)
        return guiding_folder

    @staticmethod
    def current_utc():
        return datetime.datetime.now(datetime.timezone.utc).strftime('%H-%M-%S_%f')[:-3]

    def make_guiding_root_name(self, root: str | None = None):
        if not root:
            root = filer.ram.root
        guiding_folder = os.path.join(self.make_daily_folder_name(root=root), 'Guidings')
        os.makedirs(guiding_folder, exist_ok=True)
        return os.path.join(guiding_folder, f'{PathMaker.make_seq(guiding_folder)}-{self.current_utc()}-')

    def make_acquisition_root_name(self, root: str | None = None):
        if not root:
            root = filer.ram.root
        acquisition_folder = os.path.join(self.make_daily_folder_name(root=root), 'Acquisitions')
        os.makedirs(acquisition_folder, exist_ok=True)
        return os.path.join(acquisition_folder, f'{PathMaker.make_seq(acquisition_folder)}-{self.current_utc()}-')

    def make_logfile_name(self):
        daily_folder = os.path.join(self.make_daily_folder_name(root=filer.shared.root))
        os.makedirs(daily_folder)
        return os.path.join(daily_folder, 'log.txt')

    @staticmethod
    def make_tasks_folder():
        return os.path.join(filer.shared.root, 'tasks')


path_maker = SingletonFactory.get_instance(PathMaker)


def init_log(logger_: logging.Logger, level: int | None = None, file_name: str | None = None):
    logger_.propagate = False
    level = default_log_level if level is None else level
    logger_.setLevel(level)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)-8s - {%(name)s:%(module)s:%(funcName)s:%(threadName)s:%(thread)s}' +
        ' -  %(message)s')
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger_.addHandler(handler)

    # path_maker = SingletonFactory.get_instance(PathMaker)
    file = f"{file_name}.txt" if file_name is not None else 'log.txt'
    handler = DailyFileHandler(path=os.path.join(path_maker.make_daily_folder_name(root=filer.shared.root), file),
                               mode='a')
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger_.addHandler(handler)


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


# def image_to_fits(image, path: str, header, logger_):
#     """
#
#     Parameters
#     ----------
#     image
#         an ASCOM ImageArray
#     path
#         name of the created file
#     header
#         a dictionary of FITS header key/values
#     logger_
#         a logger for logging :-)
#
#     Returns
#     -------
#
#     """
#     if not path:
#         raise 'Must supply a path to the file'
#     if not path.endswith('.fits'):
#         path += '.fits'
#
#     self.start_activity(CameraActivities.Saving)
#     hdu = fits.PrimaryHDU(data=np.transpose(image), header=fits.Header(header))
#     hdu_list = fits.HDUList([hdu])
#     logger_.info(f'saving image to {path} ...')
#     hdu_list.writeto(path, checksum=True)


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


CanonicalResponse_Ok: CanonicalResponse = CanonicalResponse(value='ok')
