"""The endpoint declaration marker and its registration helper (MAST_unit#42 invariant 10)."""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from common.canonical import CanonicalResponse
from common.endpoints import (
    Stability,
    Tier,
    UndeclaredEndpointError,
    add_api_route,
    declaration_of,
    declared_endpoints,
    endpoint,
    enveloped,
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
        # The registration helper envelopes every handler (stage 3), so the bare dict the
        # handler returns arrives under `value`.
        assert client.get("/unit/thing/status").json()["value"] == {"ok": True}


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


# --------------------------------------------------------------------------- envelope (stage 3)


class Enveloping:
    """Handlers covering every shape the wrapper has to deal with."""

    @endpoint(tier=Tier.OPERATION)
    def bare_value(self):
        return {"position": 1000}

    @endpoint(tier=Tier.OPERATION)
    def already_enveloped(self):
        return CanonicalResponse(value={"position": 1000})

    @endpoint(tier=Tier.OPERATION)
    def returns_nothing(self):
        pass

    @endpoint(tier=Tier.OPERATION)
    def raises(self):
        raise RuntimeError("driver said no")

    @endpoint(tier=Tier.OPERATION)
    def with_parameters(self, position: int, gain: int = 7):
        return {"position": position, "gain": gain}

    @endpoint(tier=Tier.OPERATION)
    async def asynchronous(self):
        return {"async": True}


def _client(*names: str) -> TestClient:
    component = Enveloping()
    router = APIRouter()
    for name in names:
        add_api_route(router, f"/{name}", endpoint=getattr(component, name), methods=["PUT"])
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_a_bare_value_is_wrapped():
    body = _client("bare_value").put("/bare_value").json()

    assert body["value"] == {"position": 1000}
    assert body["errors"] is None


def test_an_existing_envelope_is_passed_through_not_nested():
    """MAST_unit#70's hazard: a second envelope nests inside the payload and breaks consumers."""
    body = _client("already_enveloped").put("/already_enveloped").json()

    assert body["value"] == {"position": 1000}
    assert not isinstance(body["value"], dict) or "value" not in body["value"]


def test_a_handler_that_returns_nothing_still_answers_an_envelope():
    """The eight handlers that answered HTTP `null` are the reason this wrapper exists."""
    body = _client("returns_nothing").put("/returns_nothing").json()

    assert body["value"] is None
    assert body["errors"] is None
    assert body["api_version"] == "1.0"


def test_an_escaping_exception_becomes_canonical_errors():
    response = _client("raises").put("/raises")

    assert response.status_code == 200
    assert response.json()["errors"] == ["RuntimeError: driver said no"]


def test_parameters_survive_the_wrapper():
    """`functools.wraps` -- without it FastAPI reads the wrapper's own (*args, **kwargs)."""
    body = _client("with_parameters").put("/with_parameters?position=42").json()

    assert body["value"] == {"position": 42, "gain": 7}


def test_an_async_handler_is_awaited_not_serialised():
    """A sync-only wrapper would put the coroutine object into `value`."""
    body = _client("asynchronous").put("/asynchronous").json()

    assert body["value"] == {"async": True}


def test_every_wrapped_route_declares_the_envelope_as_its_200_schema():
    """53 of the unit's 72 handlers declared no return type at all before this."""
    client = _client("bare_value", "with_parameters", "asynchronous")
    schema = client.app.openapi()

    for path in ("/bare_value", "/with_parameters", "/asynchronous"):
        ref = schema["paths"][path]["put"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "CanonicalResponse" in str(ref), f"{path} does not declare the envelope: {ref}"


def test_the_declaration_survives_wrapping():
    """`functools.wraps` copies `__dict__`, so the marker rides along -- registration depends on it."""
    assert declaration_of(enveloped(Enveloping().bare_value)) is not None


def test_wrapping_does_not_mutate_the_handlers_own_annotations():
    """`functools.wraps` assigns the handler's annotations dict; a fresh one must be built."""
    before = dict(Enveloping.with_parameters.__annotations__)

    enveloped(Enveloping().with_parameters)

    assert Enveloping.with_parameters.__annotations__ == before
