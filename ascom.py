import re
import pywintypes
import win32com.client

from utils import CanonicalResponse
from abc import ABC, abstractmethod
from logging import Logger


def ascom_driver_info(driver):
    return {
        'name': driver.name,
        'description': driver.Description,
        'version': driver.DriverVersion,
    }


class AscomDispatcher(ABC):
    """
    Ensures the object has:
    - an 'ascom' property
    - a 'logger' property
    """

    @property
    @abstractmethod
    def ascom(self) -> win32com.client.Dispatch:
        pass

    @property
    @abstractmethod
    def logger(self) -> Logger:
        pass


def ascom_run(o: AscomDispatcher, sentence: str, no_entry_log=True) -> CanonicalResponse:
    ascom_dispatcher = f'{o.ascom}'
    ascom_dispatcher = re.sub('COMObject ', '', ascom_dispatcher)
    label = f'{ascom_dispatcher}.{sentence}'

    cmd = f"o.ascom.{sentence}"
    ret = None
    try:
        msg = f'{label}'
        if sentence.__contains__("="):
            exec(cmd, globals(), locals())
        else:
            ret = eval(cmd, globals(), locals())
            msg += f' -> {ret}'
        if not no_entry_log:
            o.logger.debug(msg)
        return CanonicalResponse(value=ret)

    except pywintypes.com_error as e:
        o.logger.debug(f"{label}: ASCOM error (cmd='{cmd.removeprefix('o.ascom.')}')")
        o.logger.debug(f"{label}: Message: '{e.excepinfo[2]}'")
        o.logger.debug(f"{label}:    Code: {e.hresult}")
        return CanonicalResponse(exception=e)

    except Exception as e:
        message = f"{label}: Exception: cmd='{cmd}', exception='{e}'"
        o.logger.debug(message)
        return CanonicalResponse(exception=e)