import logging
import re
import pywintypes


class AscomDriverInfo:
    """
    Gathers information of the ASCOM driver used by the current class
    """
    name: str
    description: str
    version: str

    def __init__(self, driver):
        if driver is None:
            return
        self.name = driver.Name
        self.description = driver.Description
        self.version = driver.DriverVersion

class AscomDispatcher:
    ascom: object
    logger: logging.Logger

    def __init__(self, o: object):
        self.ascom = o.ascom
        self.logger = o.logger


def ascom_run(o: object, sentence: str, no_entry_log=False):
    oo = AscomDispatcher(o)
    ascom_dispatcher = f'{oo.ascom}'
    ascom_dispatcher = re.sub('COMObject ', '', ascom_dispatcher)
    label = f'{ascom_dispatcher}.{sentence}'

    cmd = f"o.ascom.{sentence}"
    try:
        ret = None
        msg = f'{label}'
        if sentence.__contains__("="):
            exec(cmd, globals(), locals())
        else:
            ret = eval(cmd, globals(), locals())
            msg += f' -> {ret}'
        if not no_entry_log:
            oo.logger.debug(msg)
        return ret

    except pywintypes.com_error as e:
        oo.logger.debug(f'{label} ASCOM error (cmd="{cmd}": {e}')
        oo.logger.debug(f'{label} Description: {e.excepinfo[2]}')
        oo.logger.debug(f'{label}  Error code: {e.hresult}')
        oo.logger.debug(f'{label}     Message: {str(e)}')

    except Exception as e:
        oo.logger.debug(f'{label} Exception: (cmd="{cmd}") {e}')