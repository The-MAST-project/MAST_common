import logging
import platform
import datetime
import os
import io
from common.filer import Filer
from common.paths import PathMaker

default_log_level = logging.DEBUG


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


def init_log(logger_: logging.Logger, level: int | None = None, file_name: str = 'mast-unit-log.txt'):
    logger_.propagate = False
    level = default_log_level if level is None else level
    logger_.setLevel(level)

    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)-8s - {%(name)s:%(funcName)s:%(threadName)s:%(thread)s} -  %(message)s')
    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger_.addHandler(handler)

    handler = DailyFileHandler(
        path=os.path.join(
            PathMaker().make_daily_folder_name(root=Filer().shared.root),
            file_name),
        mode='a')

    handler.setLevel(level)
    handler.setFormatter(formatter)
    logger_.addHandler(handler)
