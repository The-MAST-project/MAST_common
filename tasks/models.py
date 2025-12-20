import asyncio
import json
import logging
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ValidationError

from common.api import SpecApi
from common.canonical import CanonicalResponse
from common.config import Config
from common.deep import deep_dict_update
from common.mast_logging import init_log
from common.models.assignments import (
    Initiator,
    SpectrographAssignmentModel,
)
from common.models.plans import Plan
from common.models.spectrographs import SpectrographModel
from common.spec import DeepspecBands

GatherResponse = CanonicalResponse | BaseException | None

logger = logging.getLogger("tasks")
init_log(logger)


def make_spec_model(spec_doc: dict) -> SpectrographModel | None:
    """
    Accumulates a dictionary by combining:
    - a TOML-derived dictionary (parameter) which contains the user's task description
    - defaults from the configuration database

    The resulting dictionary is fully populated, i.e. ALL the expected fields
      have a value (either from the task, or the defaults)

    :param spec_doc: a dictionary from a TOML model
    :return: a spectrograph model built from the accumulated dictionary
    """
    if "instrument" not in spec_doc:
        logger.error(f"missing 'instrument' in {spec_doc=}")
        return None
    instrument = spec_doc["instrument"]
    if instrument not in ["highspec", "deepspec"]:
        logger.error(f"bad '{instrument=}', must be either 'deepspec' or 'highspec")
        return None

    defaults = Config().get_specs()
    calibration_settings = {
        "lamp_on": spec_doc.get("lamp_on", False),
        "filter": spec_doc.get("filter"),
    }

    if instrument == "highspec":
        camera_settings = deepcopy(defaults.highspec.settings)
        if "camera" in spec_doc:
            # deep_dict_update(camera_settings, spec_doc["camera"])
            deepcopy(camera_settings, spec_doc["camera"])
        exposure_duration = defaults.highspec.settings.exposure_duration
        number_of_exposures = defaults.highspec.settings.number_of_exposures

        # propagate 'exposure_duration' and 'number_of_exposures' to the camera settings
        camera_settings.exposure_duration = exposure_duration
        camera_settings.number_of_exposures = number_of_exposures

        new_spec_dict = {
            "instrument": instrument,
            "calibration": calibration_settings,
            "exposure_duration": exposure_duration,
            "number_of_exposures": number_of_exposures,
            "spec": {
                "instrument": instrument,
                "disperser": spec_doc["disperser"],
                "camera": camera_settings,
            },
        }

    else:
        default_common_settings = defaults.deepspec["common"].settings
        assert default_common_settings is not None, (
            "empty default_camera_settings for Deepspec"
        )

        new_spec_dict = {
            "instrument": instrument,
            "calibration": calibration_settings,
            "exposure_duration": (
                spec_doc.get(
                    "exposure_duration", default_common_settings.exposure_duration
                )
            ),
            "number_of_exposures": (
                spec_doc.get(
                    "number_of_exposures", default_common_settings.number_of_exposures
                )
            ),
            "spec": {
                "instrument": instrument,
                "exposure_duration": (
                    spec_doc.get(
                        "exposure_duration", default_common_settings.exposure_duration
                    )
                ),
                "number_of_exposures": (
                    spec_doc.get(
                        "number_of_exposures",
                        default_common_settings.number_of_exposures,
                    )
                ),
                "camera": {},
            },
        }
        common_camera_settings = deepcopy(default_common_settings)
        # propagate 'exposure_duration' and 'number_of_exposures' to the camera settings
        common_camera_settings.exposure_duration = new_spec_dict["spec"][
            "exposure_duration"
        ]
        common_camera_settings.number_of_exposures = new_spec_dict["spec"][
            "number_of_exposures"
        ]

        # get band-specific camera settings
        for band in DeepspecBands.__args__:
            band_conf = deepcopy(common_camera_settings)
            if "camera" in spec_doc and band in spec_doc["camera"]:
                deep_dict_update(band_conf.model_dump(), spec_doc["camera"][band])

            new_spec_dict["spec"]["camera"][band] = band_conf

    new_spec_dict["instrument"] = instrument

    # logger.info("new_spec_dict:\n" + json.dumps(new_spec_dict, indent=2))
    try:
        spectrograph_model = SpectrographModel(**new_spec_dict)
    except ValidationError as e:
        logger.error("====== ValidationError(s) =======\n")
        for err in e.errors():
            logger.error(f"[ERR] {json.dumps(err, indent=2)}\n")
        raise
    return spectrograph_model


class TaskAcquisitionPathNotification(BaseModel):
    """
    Sent to the controller by:
    - the units, as soon as they know the path of either an 'autofocus' or 'acquisition' folder
    - the spec, as soon as it has the path of the acquisition
    """

    initiator: Initiator
    task_id: str
    src: str
    link: Literal["autofocus", "acquisition", "deepspec", "highspec", "spec"]


async def main():
    # task_file = '/Storage/mast-share/MAST/tasks/assigned/TSK_assigned_highspec_task.toml'
    plan_file = (
        "/Storage/mast-share/MAST/tasks/assigned/TSK_assigned_deepspec_task.toml"
    )
    try:
        assigned_plan: Plan = Plan.from_toml_file(plan_file)
    except ValidationError as e:
        for err in e.errors():
            logger.error(err)
        raise

    remote_assignment = assigned_plan.remote_spec_assignment
    if not remote_assignment:
        raise Exception(
            f"task '{assigned_plan.ulid}' has no spec assignment, cannot continue"
        )

    # Type assertion to help Pylance understand the spec type
    assert isinstance(remote_assignment.assignment, SpectrographAssignmentModel)
    logger.info("remote assignment: " + remote_assignment.model_dump_json(indent=2))

    spec_api = SpecApi()
    logger.info(
        f"sending task '{remote_assignment.assignment.ulid}' "
        + f"({remote_assignment.assignment.spec.instrument}) to '{spec_api.hostname}' ({spec_api.ipaddr})"
    )
    canonical_response = await spec_api.put(
        method="execute_assignment", json=remote_assignment.model_dump()
    )
    if canonical_response.succeeded:
        logger.info(
            f"[{spec_api.ipaddr}] ACCEPTED task '{remote_assignment.assignment.plan.ulid}'"
        )
    else:
        logger.error(
            f"[{spec_api.ipaddr}] REJECTED task '{remote_assignment.assignment.plan.ulid}'"
        )
        if canonical_response.errors:
            for err in canonical_response.errors:
                logger.error(f"[{spec_api.ipaddr}] {err}")


if __name__ == "__main__":
    asyncio.run(main())
