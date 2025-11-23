import logging
import re
from abc import ABC, abstractmethod

import pywintypes
import win32com.client
from pydantic import BaseModel

from common.canonical import CanonicalResponse, CanonicalResponse_Ok
from common.mast_logging import init_log

logger = logging.getLogger("mast.unit." + __name__)
init_log(logger)


def ascom_driver_info(driver):
    return {
        "name": driver.Name,
        "description": driver.Description,
        "version": driver.DriverVersion,
    }


class AscomDriverInfoModel(BaseModel):
    name: str
    description: str
    version: str
    connected: bool = False


class AscomStatus(BaseModel):
    ascom: AscomDriverInfoModel


class AscomDispatcher(ABC):
    """
    Ensures the object has:
    - an 'ascom' property
    - a 'logger' property
    """

    @property
    @abstractmethod
    def ascom(self) -> win32com.client.Dispatch: # type: ignore
        pass

    def ascom_status(self) -> AscomStatus:
        info = ascom_driver_info(self.ascom)
        response = ascom_run(self, "Connected")
        info["connected"] = response.value if response.succeeded else False
        return AscomStatus(ascom=AscomDriverInfoModel(**info))


def ascom_run(
    o: AscomDispatcher, sentence: str, no_entry_log=True
) -> CanonicalResponse:
    ascom_dispatcher = f"{o.ascom}"
    ascom_dispatcher = re.sub("COMObject ", "", ascom_dispatcher)
    label = f"{ascom_dispatcher}.{sentence}"

    cmd = f"o.ascom.{sentence}"
    ret = None
    try:
        msg = f"{label}"
        if sentence.__contains__("="):
            exec(cmd, globals(), locals())
            return CanonicalResponse_Ok
        else:
            ret = eval(cmd, globals(), locals())
            msg += f" -> {ret}"
        if not no_entry_log:
            logger.debug(msg)
        return CanonicalResponse(value=ret)

    except pywintypes.com_error as e:
        logger.debug(f"{label}: ASCOM error: cmd='{cmd.removeprefix('o.ascom.')}'")
        logger.debug(f"{label}:     Message: '{e.excepinfo[2]}'") # type: ignore
        logger.debug(f"{label}:        Code: 0x{(e.hresult & 0xffffffff):08X}") # type: ignore
        return CanonicalResponse(errors=[f"{e}"])

    except Exception as e:
        message = f"{label}: Exception: cmd='{cmd}', exception='{e}'"
        logger.debug(message)
        return CanonicalResponse(errors=[message])
