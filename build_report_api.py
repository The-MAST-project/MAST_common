"""FastAPI router exposing `/build-report` for any MAST service.

Each FastAPI app calls `make_build_report_router(workspace_root)` and includes
the returned router. Keeps the route definition in one place so every service
exposes the same schema.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from common.build_report import BuildReport, collect_build_report


def make_build_report_router(workspace_root: Path) -> APIRouter:
    """Build a router with one `GET /build-report` endpoint.

    `workspace_root` is the directory containing all `MAST_*` sibling repos
    (e.g. `/opt/MAST` on a deployed host, `/Users/.../projects/MAST` in dev).
    Each calling app passes its own resolved path; no auto-discovery magic.
    """
    router = APIRouter()
    resolved = workspace_root.resolve()

    @router.get("/build-report", response_model=BuildReport)
    def build_report() -> BuildReport:
        return collect_build_report(resolved)

    return router
