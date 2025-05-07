import logging
import time

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from common.mast_logging import init_log
from common.utils import path_maker

logger = logging.getLogger("fswatcher")
init_log(logger)


class FsWatcher:

    def __init__(self, folder: str, handlers: dict):
        self.folder = folder
        self.observer = Observer()
        self.handlers: dict = handlers
        logger.info(f"watching '{self.folder}'")

    def run(self):
        event_handler = Handler(self.handlers)
        self.observer.schedule(event_handler, self.folder, recursive=True)
        self.observer.start()
        try:
            while True:
                time.sleep(5)
        except:
            self.observer.stop()
            logger.info("Observer Stopped")

        self.observer.join()

    def stop(self):
        self.observer.stop()
        logger.info("Observer Stopped")


class Handler(FileSystemEventHandler):

    def __init__(self, handlers):
        self.handlers = handlers

    # @staticmethod
    def on_any_event(self, event):
        if event.is_directory:
            return None

        if event.event_type in self.handlers:
            # logger.info(f"handling '{event.event_type}' on '{event.src_path}'")
            self.handlers[event.event_type](event)


def just_print(event):
    logger.info(f"{event=}")


if __name__ == "__main__":
    w = FsWatcher(
        path_maker.make_plans_folder(),
        handlers={
            # 'created': just_print,
            "modified": just_print,
            "deleted": just_print,
        },
    )
    w.run()
