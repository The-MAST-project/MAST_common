import asyncio
import logging

from pydantic import BaseModel, ValidationError

from common.api import SpecApi
from common.canonical import CanonicalResponse
from common.mast_logging import init_log
from common.models.assignments import SpectrographAssignment
from common.models.plans import Plan

GatherResponse = CanonicalResponse | BaseException | None

logger = logging.getLogger("tasks")
init_log(logger)



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

    remote_assignment = assigned_plan.spec_assignment
    if not remote_assignment:
        raise Exception(
            f"plan '{assigned_plan.ulid}' has no spec assignment, cannot continue"
        )

    # Type assertion to help Pylance understand the spec type
    assert isinstance(remote_assignment, SpectrographAssignment)
    logger.info("remote assignment: " + remote_assignment.model_dump_json(indent=2))

    spec_api = SpecApi()
    logger.info(
        f"sending plan '{assigned_plan.ulid}' "
        + f"({remote_assignment.spec.instrument}) to '{spec_api.hostname}' ({spec_api.ipaddr})"
    )
    canonical_response = await spec_api.put(
        method="execute_assignment", json=remote_assignment.model_dump()
    )
    if canonical_response.succeeded:
        logger.info(f"[{spec_api.ipaddr}] ACCEPTED plan '{assigned_plan.ulid}'")
    else:
        logger.error(f"[{spec_api.ipaddr}] REJECTED plan '{assigned_plan.ulid}'")
        if canonical_response.errors:
            for err in canonical_response.errors:
                logger.error(f"[{spec_api.ipaddr}] {err}")


if __name__ == "__main__":
    asyncio.run(main())
