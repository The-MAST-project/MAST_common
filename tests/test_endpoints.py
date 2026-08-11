"""The endpoint declaration marker and its registration helper (MAST_unit#42 invariant 10)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from common.endpoints import (
    Stability,
    Tier,
    UndeclaredEndpointError,
    add_api_route,
    declaration_of,
    declared_endpoints,
    endpoint,
)


class Component:
    """Stands in for a real component: some routed methods, some not, and a hazardous property."""

    @endpoint(tier=Tier.INTERFACE)
    def status(self):
        return {"ok": True}

    @endpoint(tier=Tier.OPERATION, stability=Stability.DEPRECATED)
    def connect(self):
        return {"connected": True}

    def internal(self):
        """Not part of the HTTP surface."""

    @property
    def connected(self):
        # A real component's equivalent talks to an ASCOM driver. If a scan of this class
        # ever reads it, asking a question about the class connects a telescope.
        raise AssertionError("a scan must not evaluate properties")


class Derived(Component):
    @endpoint(tier=Tier.CONTRACT)
    def execute_assignment(self):
        return {}


def test_the_declaration_is_readable_from_the_function_and_the_bound_method():
    assert declaration_of(Component.status) == EXPECTED_STATUS
    assert declaration_of(Component().status) == EXPECTED_STATUS


EXPECTED_STATUS = declaration_of(Component.status)


def test_undeclared_methods_carry_no_declaration():
    assert declaration_of(Component.internal) is None
    assert declaration_of(lambda: None) is None


def test_declared_endpoints_enumerates_without_evaluating_properties():
    """The enumeration MAST_unit#39, #40 and #52 consume -- and it must not touch hardware."""
    found = declared_endpoints(Component())

    assert set(found) == {"status", "connect"}
    assert found["status"].tier is Tier.INTERFACE
    assert found["connect"].stability is Stability.DEPRECATED


def test_declared_endpoints_includes_inherited_declarations():
    found = declared_endpoints(Derived)

    assert set(found) == {"status", "connect", "execute_assignment"}


def test_declared_endpoints_accepts_a_class_or_an_instance():
    assert declared_endpoints(Component) == declared_endpoints(Component())


def test_registering_an_undeclared_method_fails_at_registration():
    """Invariant 10's teeth: the surface cannot be half-declared."""
    component = Component()

    with pytest.raises(UndeclaredEndpointError) as raised:
        add_api_route(APIRouter(), "/unit/thing/internal", endpoint=component.internal)

    assert "declares no tier" in str(raised.value)
    assert "internal" in str(raised.value)


def test_a_declared_route_registers_and_answers():
    component = Component()
    router = APIRouter()
    add_api_route(router, "/unit/thing/status", endpoint=component.status, tags=["Thing"])

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/unit/thing/status").json() == {"ok": True}


def _schema(*, deprecated_route: bool) -> dict:
    component = Component()
    router = APIRouter()
    add_api_route(router, "/unit/thing/status", endpoint=component.status, tags=["Thing"])
    if deprecated_route:
        add_api_route(router, "/unit/thing/connect", endpoint=component.connect, tags=["Thing"])
    app = FastAPI()
    app.include_router(router)
    return app.openapi()


def test_a_deprecated_declaration_marks_the_operation_and_nothing_else():
    """Stage 2's snapshot guard depends on this being exactly one additive key."""
    schema = _schema(deprecated_route=True)

    assert schema["paths"]["/unit/thing/connect"]["get"]["deprecated"] is True
    # The stable route beside it is untouched -- the flag is per-operation, not global.
    assert "deprecated" not in schema["paths"]["/unit/thing/status"]["get"]


def test_tags_are_passed_through_unchanged():
    """#39 replaces subsystem tags with the tier; this helper must not pre-empt it."""
    schema = _schema(deprecated_route=False)

    assert schema["paths"]["/unit/thing/status"]["get"]["tags"] == ["Thing"]


def test_the_default_method_is_get():
    router = APIRouter()
    add_api_route(router, "/unit/thing/status", endpoint=Component().status)

    assert [route.methods for route in router.routes] == [{"GET"}]


def test_methods_are_honoured():
    router = APIRouter()
    add_api_route(router, "/unit/thing/connect", endpoint=Component().connect, methods=["PUT"])

    assert [route.methods for route in router.routes] == [{"PUT"}]
