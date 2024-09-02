import logging
import re
import pywintypes
import win32com.client

from common.utils import CanonicalResponse, CanonicalResponse_Ok, init_log
from abc import ABC, abstractmethod

logger = logging.getLogger('ascom')
init_log(logger)


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

    def ascom_status(self) -> dict:
        ret = {
            'ascom': ascom_driver_info(self.ascom)
        }
        response = ascom_run(self, 'Connected')
        ret['ascom']['connected'] = response.value if response.succeeded else 'unknown'
        return ret


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
            logger.debug(msg)
        return CanonicalResponse(value=ret)

    except pywintypes.com_error as e:
        logger.debug(f"{label}: ASCOM error (cmd='{cmd.removeprefix('o.ascom.')}')")
        logger.debug(f"{label}: Message: '{e.excepinfo[2]}'")
        logger.debug(f"{label}:    Code: {e.hresult}")
        return CanonicalResponse(errors=[f"{e}"])

    except Exception as e:
        message = f"{label}: Exception: cmd='{cmd}', exception='{e}'"
        logger.debug(message)
        return CanonicalResponse(exception=e)
