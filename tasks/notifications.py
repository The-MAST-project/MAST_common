import asyncio
import pathlib

from common.tasks.models import TaskAcquisitionPathNotification, Initiator
from common.api import ControllerApi
from pathlib import Path
from common.filer import Filer


def notify_controller_about_task_acquisition_path(
    task_id: str, src: str | Path, link: str
):
    if isinstance(src, pathlib.WindowsPath):
        src = str(src.as_posix()).replace("Z:/MAST", "/Storage/mast-share/MAST")
    elif isinstance(src, str):
        src = str(Path(src).as_posix()).replace("Z:/MAST", "/Storage/mast-share/MAST")

    notification: TaskAcquisitionPathNotification = TaskAcquisitionPathNotification(
        initiator=Initiator.local_machine(),
        task_id=task_id,
        src=str(src),
        link="spec",
    )
    controller_api = ControllerApi()
    asyncio.run(
        controller_api.client.put(
            "task_acquisition_path_notification", data=notification.model_dump_json()
        )
    )


def main():
    notify_controller_about_task_acquisition_path(
        task_id="01jknm5pywgsh9f3v61w4ybffy",
        src="Z:/MAST/mast-wis-spec/2025-02-25/deepspec/acquisition-0007",
        link="spec",
    )


if __name__ == "__main__":
    asyncio.run(main())
