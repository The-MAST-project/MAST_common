"""The plan token's refusal rule (plans/control_acquisition_design.md 6.6)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.plan_token import is_refused, plan_ownership_middleware

PLAN, OTHER = "01PLAN", "01OTHER"


@pytest.mark.parametrize(
    ("method", "path", "carried", "owner", "refused"),
    [
        ("PUT", "/unit/start_acquisition_and_guiding", None, None, False),  # nobody owns it
        ("PUT", "/unit/start_acquisition_and_guiding", PLAN, PLAN, False),  # the owner's token
        ("PUT", "/unit/start_acquisition_and_guiding", OTHER, PLAN, True),  # another plan's
        ("PUT", "/unit/mount/park", None, PLAN, True),  # an operator's command, no token
        ("POST", "/unit/expose", None, PLAN, True),
        ("GET", "/unit/status", None, PLAN, False),  # reads always pass
        ("GET", "/unit/focus_on_star_status", OTHER, PLAN, False),
        ("PUT", "/unit/abort", None, PLAN, False),  # abort always passes
        ("PUT", "/unit/mount/abort/", OTHER, PLAN, False),
    ],
)
def test_the_rule(method, path, carried, owner, refused):
    assert is_refused(method, path, carried, owner) is refused


def test_over_http():
    owner = {"plan": PLAN}
    app = FastAPI()
    app.middleware("http")(plan_ownership_middleware(lambda: owner["plan"]))

    @app.put("/unit/park")
    def park():
        return {"api_version": "1.0", "value": "ok"}

    @app.get("/unit/status")
    def status():
        return {"api_version": "1.0", "value": "status"}

    client = TestClient(app)
    refused = client.put("/unit/park").json()
    assert refused["errors"] == ["/unit/park: refused, already working on plan 01PLAN"]
    assert client.put("/unit/park", params={"plan_id": PLAN}).json()["value"] == "ok"
    assert client.get("/unit/status").json()["value"] == "status"
    owner["plan"] = None
    assert client.put("/unit/park").json()["value"] == "ok", "released: anything goes again"
