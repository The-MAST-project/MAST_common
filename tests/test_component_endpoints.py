"""The component-interface verbs are generated from the ABC (MAST_unit#40).

A route is in the interface contract iff this generator emitted it. What the tests pin is that
provenance: the verb set comes from the ABC's declarations rather than a literal tuple here, the
paths and methods follow from those declarations, and a component's own concrete override --
which carries no marker of its own -- is still registered.
"""

from __future__ import annotations

from enum import IntFlag, auto

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from common.activities import Activities
from common.endpoints import (
    TIER_TAGS,
    Completion,
    Tier,
    UndeclaredEndpointError,
    declared_endpoints,
    endpoint,
    register_component_endpoints,
)
from common.interfaces.components import Component

INTERFACE_VERBS = {"startup", "shutdown", "abort", "status"}


class Thing(Component):
    """A component with the ABC's verbs implemented and nothing decorated of its own."""

    def __init__(self):
        Activities.__init__(self)
        self.calls: list[str] = []

    def startup(self):
        self.calls.append("startup")
        return {"started": True}

    def shutdown(self):
        self.calls.append("shutdown")
        return {"stopped": True}

    def abort(self):
        self.calls.append("abort")
        return {"aborted": True}

    def status(self):
        return {"connected": True}

    @property
    def is_shutting_down(self) -> bool:
        return False

    def powerdown(self):
        pass

    @property
    def name(self) -> str:
        return "thing"

    @name.setter
    def name(self, value: str):
        pass

    @property
    def operational(self) -> bool:
        return True

    @property
    def why_not_operational(self) -> list[str]:
        return []

    @property
    def detected(self) -> bool:
        return True

    @property
    def connected(self) -> bool:
        return True

    @property
    def was_shut_down(self) -> bool:
        return False

    @operational.setter
    def operational(self, value: str) -> bool:
        return True


def _router(**kwargs) -> APIRouter:
    """The router itself, not the app: `include_router` appends an opaque `_IncludedRouter`
    to `app.routes` on FastAPI 0.139, so mounted routes are not enumerable there."""
    router = APIRouter()
    register_component_endpoints(router, Thing(), "/unit/thing", **kwargs)
    return router


def _app(**kwargs) -> FastAPI:
    app = FastAPI()
    app.include_router(_router(**kwargs))
    return app


def test_the_verb_set_comes_from_the_abc():
    """Not a literal tuple in the generator: a fifth verb is one decorated method on Component."""
    assert set(declared_endpoints(Component)) == INTERFACE_VERBS


def test_every_interface_verb_is_registered_under_the_base_path():
    paths = {route.path for route in _router().routes}

    assert paths == {f"/unit/thing/{verb}" for verb in INTERFACE_VERBS}


def test_the_methods_come_from_the_declaration():
    by_path = {route.path: route.methods for route in _router().routes}

    assert by_path["/unit/thing/startup"] == {"PUT"}
    assert by_path["/unit/thing/shutdown"] == {"PUT"}
    assert by_path["/unit/thing/abort"] == {"PUT"}
    assert by_path["/unit/thing/status"] == {"GET"}


def test_a_concrete_override_carries_no_marker_and_is_registered_anyway():
    """The declaration lives on the ABC; the method a component supplies is a plain override."""
    from common.endpoints import declaration_of

    assert declaration_of(Thing.startup) is None

    assert "/unit/thing/startup" in {route.path for route in _router().routes}


def test_the_generated_routes_are_tagged_and_enveloped():
    schema = _app().openapi()
    operation = schema["paths"]["/unit/thing/status"]["get"]

    assert operation["tags"] == [TIER_TAGS[Tier.INTERFACE]]
    assert operation["x-stability"] == "interface"


def test_a_generated_route_answers_through_the_envelope():
    with TestClient(_app()) as client:
        assert client.get("/unit/thing/status").json()["value"] == {"connected": True}
        assert client.put("/unit/thing/abort").json()["value"] == {"aborted": True}


def test_the_generator_refuses_to_emit_an_empty_surface(monkeypatch):
    """A silent no-op here would unregister the whole interface contract."""
    import common.endpoints as endpoints_module

    monkeypatch.setattr(endpoints_module, "declared_endpoints", lambda target: {})

    with pytest.raises(endpoints_module.UndeclaredEndpointError):
        register_component_endpoints(APIRouter(), Thing(), "/unit/thing")


# ------------------------------------------------------ per-component completion (#43, #157)


class ThingActivities(IntFlag):
    StartingUp = auto()


class FlaggingThing(Thing):
    """A component whose startup is asynchronous, and says so on its own implementation."""

    @endpoint(tier=Tier.INTERFACE, completion=ThingActivities.StartingUp)
    def startup(self):
        return {"started": True}


class ContradictingThing(Thing):
    @endpoint(tier=Tier.OPERATION, completion=Completion.IMMEDIATE)
    def startup(self):
        return {}


def _schema_for(component) -> dict:
    router = APIRouter()
    register_component_endpoints(router, component, "/unit/thing")
    app = FastAPI()
    app.include_router(router)
    return app.openapi()["paths"]


def test_a_component_states_how_its_own_startup_finishes():
    """The tier is uniform across components; the completion signal is not (#157)."""
    paths = _schema_for(FlaggingThing())

    assert paths["/unit/thing/startup"]["put"]["x-completion"] == "activity:StartingUp"


def test_a_component_that_says_nothing_keeps_the_abc_declaration():
    paths = _schema_for(Thing())

    assert "x-completion" not in paths["/unit/thing/startup"]["put"]
    assert paths["/unit/thing/startup"]["put"]["tags"] == [TIER_TAGS[Tier.INTERFACE]]


def test_an_override_may_not_contradict_the_tier():
    """Refining completion is the point; re-tiering an interface verb is the drift to prevent."""
    with pytest.raises(UndeclaredEndpointError, match="not its tier"):
        register_component_endpoints(APIRouter(), ContradictingThing(), "/unit/thing")
