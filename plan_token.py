"""The plan token: whom a unit or the spectrograph is working for (plans/control_acquisition_design.md 6.6).

MAST_control is the single authority on plans, and only one is executed at a time. It hands
each participant the plan's ULID as `plan_id`, a query parameter on every request it sends
on the plan's behalf. The first such request makes the participant the plan's: it raises its
`ExecutingPlan` umbrella and keeps the token. From then on, until an abort releases it, a
state-changing request that does not carry the same token is refused -- so no second plan,
and no operator command, can reach it mid-plan. Reads (`GET`) and `abort` always pass.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from common.canonical import CanonicalResponse

#: The query parameter that carries the token, on the unit's and the spectrograph's routes.
PLAN_ID_PARAM = "plan_id"

#: Never refused: reading changes nothing, and abort is how an owner is released.
PASSING_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_refused(method: str, path: str, carried: str | None, owner: str | None) -> bool:
    """Whether a request is refused while ``owner`` (None: nobody) holds the participant."""
    if owner is None or method.upper() in PASSING_METHODS:
        return False
    if path.rstrip("/").endswith("/abort"):
        return False
    return carried != owner


def plan_ownership_middleware(owner: Callable[[], str | None]):
    """A FastAPI HTTP middleware refusing what `is_refused` refuses. ``owner`` answers the
    plan the participant works for, None when none -- asked on every request, so it is never
    stale. Install it with `app.middleware("http")(...)`, as the unit installs its shutdown gate."""

    async def refuse_other_plans(request: Request, call_next):
        current = owner()
        if is_refused(request.method, request.url.path, request.query_params.get(PLAN_ID_PARAM), current):
            return JSONResponse(
                CanonicalResponse(errors=[f"{request.url.path}: refused, already working on plan {current}"]).model_dump()
            )
        return await call_next(request)

    return refuse_other_plans
