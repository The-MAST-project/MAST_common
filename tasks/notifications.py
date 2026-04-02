import asyncio
import pathlib
from pathlib import Path

from common.api import ControllerApi
from common.tasks.models import AcquisitionPathNotification, AcquisitionSubpath, Initiator


def notify_controller_about_acquisition_path(
    assignment_id: str, path_on_share: str | Path, subpath: AcquisitionSubpath
):
    if isinstance(path_on_share, pathlib.WindowsPath):
        path_on_share = str(path_on_share.as_posix()).replace(
            "Z:/MAST", "/Storage/mast-share/MAST"
        )
    elif isinstance(path_on_share, str):
        path_on_share = str(Path(path_on_share).as_posix()).replace(
            "Z:/MAST", "/Storage/mast-share/MAST"
        )

    notification: AcquisitionPathNotification = AcquisitionPathNotification(
        initiator=Initiator.local_machine(),
        assignment_id=assignment_id,
        src=str(path_on_share),
        subpath=subpath,
    )

    controller_api = ControllerApi()
    assert controller_api.client is not None
    asyncio.run(
        controller_api.client.put(
            "task_acquisition_path_notification", data=notification.model_dump()
        )
    )


async def main():
    notify_controller_about_acquisition_path(
        assignment_id="01jknm5pywgsh9f3v61w4ybffy",
        path_on_share="Z:/MAST/mast-wis-spec/2025-02-25/deepspec/acquisition-0007",
        subpath="spec",
    )


if __name__ == "__main__":
    asyncio.run(main())
