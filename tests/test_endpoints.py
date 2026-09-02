"""The endpoint declaration marker and its registration helper (MAST_unit#42 invariant 10)."""

from __future__ import annotations

from enum import IntFlag, auto

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from common.canonical import CanonicalResponse
from common.endpoints import (
    OPENAPI_TAGS,
    TIER_GROUPS,
    TIER_STABILITY,
    TIER_TAGS,
    Completion,
    EndpointDeclaration,
    NotificationChannel,
    Stability,
    Tier,
    UndeclaredEndpointError,
    add_api_route,
    area_of,
    completion_token,
    declaration_of,
    declared_endpoints,
    display_tag,
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
    add_api_route(router, "/unit/thing/status", endpoint=component.status)

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        # The registration helper envelopes every handler (stage 3), so the bare dict the
        # handler returns arrives under `value`.
        assert client.get("/unit/thing/status").json()["value"] == {"ok": True}


def _schema(*, deprecated_route: bool) -> dict:
    component = Component()
    router = APIRouter()
    add_api_route(router, "/unit/thing/status", endpoint=component.status)
    if deprecated_route:
        add_api_route(router, "/unit/thing/connect", endpoint=component.connect)
    app = FastAPI()
    app.include_router(router)
    return app.openapi()


def test_a_deprecated_declaration_marks_the_operation_and_nothing_else():
    """Stage 2's snapshot guard depends on this being exactly one additive key."""
    schema = _schema(deprecated_route=True)

    assert schema["paths"]["/unit/thing/connect"]["get"]["deprecated"] is True
    # The stable route beside it is untouched -- the flag is per-operation, not global.
    assert "deprecated" not in schema["paths"]["/unit/thing/status"]["get"]


def test_the_tag_is_the_tier():
    """#39: one tag per route, and it is the tier -- the path prefix already carries the layer."""
    schema = _schema(deprecated_route=False)

    assert schema["paths"]["/unit/thing/status"]["get"]["tags"] == [TIER_TAGS[Tier.INTERFACE]]


def test_a_caller_cannot_file_a_route_under_its_own_tag():
    """A `tags=` argument is ignored, not refused: the declaration wins and registration proceeds.

    Refusing it would make adopting this helper a flag day for MAST_spec's 29 `tags=` call sites
    and MAST_control's 9, which is a high price for a group name the declaration already knows.
    """
    router = APIRouter()
    add_api_route(router, "/unit/thing/status", endpoint=Component().status, tags=["Thing"])
    app = FastAPI()
    app.include_router(router)

    assert app.openapi()["paths"]["/unit/thing/status"]["get"]["tags"] == [TIER_TAGS[Tier.INTERFACE]]


def test_the_tier_is_published_as_x_stability():
    """The machine-readable half: a consumer's contract test can assert what it calls."""
    schema = _schema(deprecated_route=True)

    assert schema["paths"]["/unit/thing/status"]["get"]["x-stability"] == "interface"
    assert schema["paths"]["/unit/thing/connect"]["get"]["x-stability"] == "operator"


def test_a_demo_route_is_struck_through():
    """DEMO is parked, so it renders deprecated without needing a stability of its own."""

    class Demo:
        @endpoint(tier=Tier.DEMO)
        def dance(self):
            return {}

    router = APIRouter()
    add_api_route(router, "/unit/mount/dance", endpoint=Demo().dance)
    app = FastAPI()
    app.include_router(router)
    operation = app.openapi()["paths"]["/unit/mount/dance"]["get"]

    assert operation["deprecated"] is True
    assert operation["tags"] == [TIER_TAGS[Tier.DEMO]]
    assert operation["x-stability"] == "demo"


def test_every_tier_has_a_tag_a_stability_and_a_described_group():
    """A tier added without its display metadata would raise a KeyError at registration."""
    assert set(TIER_TAGS) == set(Tier)
    assert set(TIER_STABILITY) == set(Tier)
    assert set(TIER_GROUPS) == set(Tier)
    assert [group["name"] for group in TIER_GROUPS.values()] == [TIER_TAGS[tier] for tier in Tier]
    assert all(group["description"] for group in TIER_GROUPS.values())
    assert [TIER_GROUPS[tier] for tier in Tier] == OPENAPI_TAGS


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/mast/api/v1/unit/mount/park", "mount"),
        ("/mast/api/v1/unit/expose", "unit"),
        ("/mast/api/v1/spec/stage/position", "stage"),
        ("/unit/thing/status", "thing"),
        ("/status", None),
    ],
)
def test_the_area_is_the_segment_before_the_verb(path, expected):
    """#207: a layer-2 route files under its component, a layer-1 one under the service."""
    assert area_of(path) == expected


def test_only_the_operator_tier_is_split_by_area():
    """#207: the two contract tiers stay one group each, and DEMO is three parked routes."""
    path = "/mast/api/v1/unit/mount/park"

    assert display_tag(Tier.OPERATION, path) == "Mount — operator"
    assert display_tag(Tier.CONTRACT, path) == TIER_TAGS[Tier.CONTRACT]
    assert display_tag(Tier.INTERFACE, path) == TIER_TAGS[Tier.INTERFACE]
    assert display_tag(Tier.DEMO, path) == TIER_TAGS[Tier.DEMO]


def test_an_operator_route_with_no_area_keeps_the_flat_tier_tag():
    """A single-segment path has no segment before its verb, so there is no area to name."""
    assert display_tag(Tier.OPERATION, "/status") == TIER_TAGS[Tier.OPERATION]


def test_the_registered_tag_is_the_area_the_route_is_served_at():
    """#207: the group cannot disagree with the path, because it is derived from it."""
    router = APIRouter()
    add_api_route(router, "/mast/api/v1/unit/mount/park", endpoint=Component().connect, methods=["PUT"])
    app = FastAPI()
    app.include_router(router)

    operation = app.openapi()["paths"]["/mast/api/v1/unit/mount/park"]["put"]

    assert operation["tags"] == ["Mount — operator"]
    assert operation["x-stability"] == "operator"


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


# ----------------------------------------------------------------- handlers built at registration


class WithFactory:
    """A component whose handler must be built after construction, not defined at import.

    Stands in for MAST_unit's spiral-search endpoint: the operator-facing defaults are the
    unit's own configured fibre position, which `Config()` has not loaded when the class body
    runs. Binding them into a closure's signature is what puts real numbers in Swagger.
    """

    def __init__(self, configured_center: int):
        self.configured_center = configured_center

    @endpoint(tier=Tier.OPERATION, factory=True)
    def _new_path_endpoint(self):
        configured = self.configured_center

        def new_path(steps: int, center: int = configured):
            return {"steps": steps, "center": center}

        return new_path

    @endpoint(tier=Tier.OPERATION, stability=Stability.DEPRECATED, factory=True)
    def _old_path_endpoint(self):
        def old_path():
            return {}

        return old_path

    @endpoint(tier=Tier.OPERATION, factory=True)
    def _not_a_handler(self):
        return {"built": "nothing callable"}


def test_a_factory_built_handler_is_declared_and_registers():
    """The refusal must not fire on a handler that was built rather than defined."""
    router = APIRouter()

    add_api_route(router, "/unit/thing/new_path", endpoint=WithFactory(512)._new_path_endpoint(), methods=["PUT"])

    assert [route.path for route in router.routes] == ["/unit/thing/new_path"]


def test_the_factory_itself_stays_enumerable():
    """Invariant 10's other half: a factory endpoint must not vanish from the surface.

    `declared_endpoints` walks class attributes, and the handler is not one -- so the
    declaration has to ride on the factory as well, or #39, #40 and #52 would each read a
    surface with a hole in it exactly where the prefix used to leave one.
    """
    declared = declared_endpoints(WithFactory)

    assert declared["_new_path_endpoint"].tier is Tier.OPERATION


def test_the_built_handler_carries_the_declaration_too():
    handler = WithFactory(512)._new_path_endpoint()

    assert declaration_of(handler) == EndpointDeclaration(tier=Tier.OPERATION)


def test_the_configured_default_reaches_the_openapi_schema():
    """The reason factories exist at all -- an import-time default could not know this."""
    router = APIRouter()
    add_api_route(router, "/unit/thing/new_path", endpoint=WithFactory(512)._new_path_endpoint(), methods=["PUT"])
    app = FastAPI()
    app.include_router(router)

    parameters = app.openapi()["paths"]["/unit/thing/new_path"]["put"]["parameters"]
    center = next(parameter for parameter in parameters if parameter["name"] == "center")

    assert center["schema"]["default"] == 512
    # Two instances, two schemas: the default is per-unit, which a signature default is not.
    assert (
        next(parameter for parameter in _factory_parameters(WithFactory(4096)) if parameter["name"] == "center")["schema"][
            "default"
        ]
        == 4096
    )


def _factory_parameters(component: WithFactory) -> list[dict]:
    router = APIRouter()
    add_api_route(router, "/unit/thing/new_path", endpoint=component._new_path_endpoint(), methods=["PUT"])
    app = FastAPI()
    app.include_router(router)
    return app.openapi()["paths"]["/unit/thing/new_path"]["put"]["parameters"]


def test_a_factory_built_route_answers_through_the_envelope():
    router = APIRouter()
    add_api_route(router, "/unit/thing/new_path", endpoint=WithFactory(512)._new_path_endpoint(), methods=["PUT"])
    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        assert client.put("/unit/thing/new_path?steps=3").json()["value"] == {"steps": 3, "center": 512}


def test_stability_rides_through_the_factory():
    router = APIRouter()
    add_api_route(router, "/unit/thing/old_path", endpoint=WithFactory(512)._old_path_endpoint(), methods=["PUT"])
    app = FastAPI()
    app.include_router(router)

    assert app.openapi()["paths"]["/unit/thing/old_path"]["put"]["deprecated"] is True


def test_a_factory_that_returns_no_handler_is_refused_where_it_happened():
    """Without this the failure would surface inside FastAPI, naming neither the factory nor why."""
    with pytest.raises(UndeclaredEndpointError) as raised:
        WithFactory(512)._not_a_handler()

    assert "factory=True" in str(raised.value)
    assert "_not_a_handler" in str(raised.value)


def test_the_factory_keeps_its_own_identity():
    """`functools.wraps`: a traceback through the factory must still name the factory."""
    assert WithFactory._new_path_endpoint.__name__ == "_new_path_endpoint"


# --------------------------------------------------------------------- completion (#43 stage 2)


class FakeActivities(IntFlag):
    Slewing = auto()
    Parking = auto()


class Timed:
    """One handler per completion form."""

    @endpoint(tier=Tier.OPERATION, completion=Completion.IMMEDIATE)
    def read_position(self):
        return {"position": 1000}

    @endpoint(tier=Tier.OPERATION, completion=Completion.BLOCKING)
    def stop_tracking(self):
        return {}

    @endpoint(tier=Tier.OPERATION, completion=FakeActivities.Slewing)
    def goto(self):
        return {}

    @endpoint(tier=Tier.OPERATION, completion=NotificationChannel.ASSIGNMENT)
    def execute_assignment(self):
        return {}

    @endpoint(tier=Tier.OPERATION)
    def undeclared(self):
        return {}


def _operation(name: str) -> dict:
    router = APIRouter()
    add_api_route(router, f"/{name}", endpoint=getattr(Timed(), name), methods=["PUT"])
    app = FastAPI()
    app.include_router(router)
    return app.openapi()["paths"][f"/{name}"]["put"]


def test_an_immediate_operation_says_so():
    assert _operation("read_position")["x-completion"] == "immediate"


def test_a_blocking_operation_says_so():
    """Category C exists -- a declaration that could only name a flag could not describe it."""
    assert _operation("stop_tracking")["x-completion"] == "blocking"


def test_an_activity_flag_renders_as_the_name_a_client_polls():
    """`activities_verbal` reports bare member names, so that is what the token has to match."""
    assert _operation("goto")["x-completion"] == "activity:Slewing"


def test_a_notification_channel_names_the_stream_to_watch():
    """Two channels exist, so a bare `notification` would not tell a client which one to read."""
    assert _operation("execute_assignment")["x-completion"] == "notification:assignment_notification"


def test_an_undeclared_completion_publishes_nothing():
    """Absent rather than defaulted: the contract check reports it, silence would hide it."""
    assert "x-completion" not in _operation("undeclared")


def test_a_composite_flag_is_refused():
    """ "Watch these two bits" is not a completion signal a client can act on."""
    with pytest.raises(ValueError, match="one activity flag"):
        completion_token(FakeActivities.Slewing | FakeActivities.Parking)


def test_a_zero_valued_idle_is_refused():
    """`Idle = 0` names the absence of activity, not the end of one."""

    class WithIdle(IntFlag):
        Idle = 0
        Moving = auto()

    with pytest.raises(ValueError, match="exactly one activity flag"):
        completion_token(WithIdle.Idle)
