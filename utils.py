from abc import abstractmethod
from enum import IntFlag
from threading import Timer, Lock
import logging
import platform
import os
import io
import astropy.io.fits as fits
from multiprocessing import shared_memory
import re
from abc import ABC
import sys
import traceback

# from config import Config
import datetime
from typing import List, Any

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


class PathMaker:
    top_folder: str

    def __init__(self):
        # cfg = Config()
        # self.top_folder = cfg.toml['global']['TopFolder']
        self.top_folder = 'C:\\MAST'
        pass

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
            seq_file = os.path.join(folder, f'.{camera}.seq')
        else:
            seq_file = os.path.join(folder, '.seq')

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

    def make_daily_folder_name(self):
        d = os.path.join(self.top_folder, datetime.datetime.now().strftime('%Y-%m-%d'))
        os.makedirs(d, exist_ok=True)
        return d

    def make_exposure_file_name(self, camera: str, acquisition: str | None = None):
        if acquisition:
            folder = self.make_acquisition_folder_name(acquisition)
        else:
            folder = os.path.join(self.make_daily_folder_name(), 'Exposures')
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f'exposure-{camera}-{path_maker.make_seq(folder)}')

    def make_acquisition_folder_name(self, acquisition: str = None):
        acquisitions_folder = os.path.join(self.make_daily_folder_name(), 'Acquisitions')
        os.makedirs(acquisitions_folder, exist_ok=True)
        if acquisition is None:
            path = os.path.join(acquisitions_folder, f'acquisition-{PathMaker.make_seq(folder=acquisitions_folder)}')
        else:
            path = os.path.join(acquisitions_folder, f"{acquisition}")
        return path

    def make_guiding_folder_name(self):
        guiding_folder = os.path.join(self.make_daily_folder_name(), 'Guidings')
        os.makedirs(guiding_folder, exist_ok=True)
        return os.path.join(guiding_folder, f'guiding-{PathMaker.make_seq(guiding_folder)}')

    def make_logfile_name(self):
        daily_folder = os.path.join(self.make_daily_folder_name())
        os.makedirs(daily_folder)
        return os.path.join(daily_folder, 'log.txt')

    def make_tasks_folder(self):
        return os.path.join(self.top_folder, 'tasks')


path_maker = SingletonFactory.get_instance(PathMaker)


def init_log(logger_: logging.Logger, level: int | None = None, file_name: str | None = None):
    logger_.propagate = False
    level = default_log_level if level is None else level
    logger_.setLevel(level)

    formatter = logging.Formatter('%(asctime)s - %(levelname)-8s - {%(name)s:%(funcName)s:%(threadName)s:%(thread)s}' +
                                  ' -  %(message)s')
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger_.addHandler(handler)

    # path_maker = SingletonFactory.get_instance(PathMaker)
    file = f"{file_name}.txt" if file_name is not None else 'log.txt'
    handler = DailyFileHandler(path=os.path.join(path_maker.make_daily_folder_name(), file), mode='a')
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


def image_to_fits(image, path: str, header: dict, logger_):
    """

    Parameters
    ----------
    image
        an ASCOM ImageArray
    path
        name of the created file
    header
        a dictionary of FITS header key/values
    logger_
        a logger for logging :-)

    Returns
    -------

    """
    if not path:
        raise 'Must supply a path to the file'
    if not path.endswith('.fits'):
        path += '.fits'

    hdu = fits.PrimaryHDU(image)
    for k, v in header.items():
        hdu.header[k] = v
    hdu_list = fits.HDUList([hdu])
    logger_.info(f'saving image to {path} ...')
    hdu_list.writeto(path)


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


class CanonicalResponse:
    """
    Formalizes API responses.  An API method will return a CanonicalResponse, so that the
     API caller may safely parse it.

    An API method may ONLY return one of the following keys (in decreasing severity): 'exception', 'errors' or 'value'

    - 'exception' - an exception occurred, delivers the details (no 'value')
    - 'errors' - the method detected one or more errors (no 'value')
    - 'value' - all went well, this is the return value (may be 'None')
    """

    ok: dict = {'value': 'ok'}

    def __init__(self,
                 value: Any = None,
                 errors: List[str] | str | None = None,
                 exception: Exception | None = None
                 ):

        if exception:
            exc_type, exc_value, exc_traceback = sys.exc_info()
            traceback_string = traceback.format_exception(exc_type, exc_value, exc_traceback)
            self.exception = traceback_string
        elif errors:
            self.errors = errors
        else:
            self.value = value

    @property
    def is_error(self):
        return self.errors is not None

    @property
    def is_exception(self):
        return self.exception is not None

    @property
    def succeeded(self):
        return self.value is not None

    @property
    def failed(self):
        return self.errors or self.exception

    @property
    def failure(self) -> List[str] | str | None:
        if not self.failed:
            return None
        if self.is_exception:
            return self.exception
        if self.is_error:
            return self.errors if self.errors is not None else None
