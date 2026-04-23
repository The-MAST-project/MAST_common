from pydantic import BaseModel

from .batches import Batch
from .plans import Plan


class Workload(BaseModel):
    work: Plan | Batch

    async def expose(self):
        from common.models.assignments import Initiator, SpectrographAssignment

        assert self.work.spec_assignment is not None
        assert self.work.spec_assignment.instrument is not None
        assert self.work.spec_api is not None

        spec_assignment = SpectrographAssignment(
            instrument=self.work.spec_assignment.instrument,
            initiator=Initiator.local_machine(),
            plan=self.work if isinstance(self.work, Plan) else None,
            batch=self.work if isinstance(self.work, Batch) else None,
            spec=self.work.spec_assignment,
        )
        canonical_response = await self.work.spec_api.put(
            method="execute_assignment", json=spec_assignment.model_dump()
        )
        if canonical_response.succeeded:
            pass  # caller logs as needed
        else:
            canonical_response.log(label="spec")
            await self.work.terminate(
                reason="failed", details=["spec rejected assignment"]
            )
