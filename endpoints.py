"""Definition-site declaration of the HTTP surface (MAST_unit#42, invariant 10).

An endpoint declares itself, and its contract tier, **where it is defined** -- not by being
named a certain way, and not only by appearing in a router body.

## Why this replaces the `endpoint_` prefix

The prefix was chosen so that scanning a component's method names would reveal its HTTP
surface. Measured on MAST_unit `65a1b96` it did not: of 73 routed operations, **26 were
registered on bare, unprefixed methods** -- and ten `endpoint_`-named methods were routed by
nothing at all. A convention that looks authoritative and is wrong in both directions is
worse than none, because it is trusted.

What a marker gives that a name cannot:

- **The surface is enumerable at runtime.** `declared_endpoints(component)` answers in one
  call. MAST_unit#39 (tier tags in Swagger), #40 (generated INTERFACE routes) and #52 (the
  routes-to-manifest anchor) each had to rediscover the route set by reading `api_router`
  bodies; they now read one source.
- **Registering an undeclared method is an import-time error** (`add_api_route` below), so
  the declaration cannot silently drift out of step with what is served. That is the specific
  failure the prefix suffered.
- **The tier is a fact a test can assert**, not a convention a reader must trust.

The prefix's retirement was ratified 2026-08-10, conditional on the marker actually
delivering the quick-find property the prefix was chosen for: one literal `@endpoint(` grep
returns the surface exactly, with no aliases and no conditional application. Keep it that
way -- do not add a bare `@endpoint` form, and do not wrap this decorator in another. A
handler built at registration time (`factory=True`, below) is declared with the same token
for that reason: a second decorator named anything else would put a hole in the grep on day
one.

`api_router` stays the human-scannable per-component index: path and verb remain visible
there, one line per route. Only the *tier* moves to the definition site.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntFlag, StrEnum
from typing import Any

from fastapi import APIRouter

from common.canonical import CanonicalResponse
from common.mast_logging import get_logger

logger = get_logger(__name__)

# The attribute the decorator sets. Named with dunders so it cannot collide with anything a
# component defines, and read through `declaration_of` rather than directly.
MARKER = "__mast_endpoint__"


class Tier(StrEnum):
    """The contract tiers, in the order they are presented (drives MAST_unit#39's grouping).

    CONTRACT    unit orchestration: the whole programmatic surface for observing.
    OPERATION   bespoke operator / diagnostic verbs; the day-to-day manual surface.
    INTERFACE   component lifecycle -- startup / shutdown / abort / status, ABC-enforced.
    DEMO        the dancing endpoints (parked).
    """

    CONTRACT = "CONTRACT"
    OPERATION = "OPERATION"
    INTERFACE = "INTERFACE"
    DEMO = "DEMO"


class Stability(StrEnum):
    """Whether a route is here to stay.

    DEPRECATED is a retirement notice, not a synonym for unused: it renders struck through
    in Swagger, which reaches the operators a code search cannot. It is how the eleven routes
    MAST_unit#124 removes are announced before they go.
    """

    STABLE = "stable"
    DEPRECATED = "deprecated"


#: Swagger group per tier. One tag per route, and it is the tier -- the path prefix already
#: carries the layer (`/unit/<verb>` vs `/unit/<component>/<verb>`), so a subsystem tag would
#: only repeat it while saying nothing about what a consumer may depend on.
TIER_TAGS: dict[Tier, str] = {
    Tier.CONTRACT: "Unit orchestration (contract)",
    Tier.OPERATION: "Operator / diagnostic operations",
    Tier.INTERFACE: "Component interface (contract)",
    Tier.DEMO: "Demonstration (parked)",
}

#: The machine-readable half, published per operation as `x-stability`. A consumer's contract
#: test can assert it calls only `contract` and `interface` routes and fail when it reaches
#: for an operator verb -- which a human-readable tag cannot support.
TIER_STABILITY: dict[Tier, str] = {
    Tier.CONTRACT: "contract",
    Tier.OPERATION: "operator",
    Tier.INTERFACE: "interface",
    Tier.DEMO: "demo",
}

#: `openapi_tags` for the app, in display order: importance and utility, so the operator
#: surface sits above the uniform lifecycle verbs.
OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": TIER_TAGS[Tier.CONTRACT],
        "description": "The programmatic surface for observing. Build clients on these.",
    },
    {
        "name": TIER_TAGS[Tier.OPERATION],
        "description": (
            "Bespoke operator and diagnostic verbs, for driving a unit by hand. "
            "**Not a contract** -- these may change without notice."
        ),
    },
    {
        "name": TIER_TAGS[Tier.INTERFACE],
        "description": (
            "The lifecycle verbs every component answers -- startup, shutdown, abort, status. "
            "Uniform across components, and safe to build on."
        ),
    },
    {
        "name": TIER_TAGS[Tier.DEMO],
        "description": "Demonstration routes, parked. Shown struck through; do not call them.",
    },
]


class Completion(StrEnum):
    """How a caller learns that an operation has finished (MAST_unit#42, invariant 3).

    A third form is expressed by passing an activity flag itself rather than a member of this
    enum: the operation returns at once and the caller watches that flag clear in `status`.
    """

    IMMEDIATE = "immediate"  # finished when the response arrives
    BLOCKING = "blocking"  # the response is withheld until the hardware is done


def completion_token(completion: Completion | IntFlag) -> str:
    """The `x-completion` value published for a declaration.

    An activity flag renders as `activity:<Name>`, and the name is deliberately the one that
    appears in `activities_verbal` -- the field a client actually polls.

    Exactly one member: "watch these two bits" is not a signal a client can act on, and a
    zero-valued `Idle` names the absence of activity rather than the end of one. Counting
    members rather than checking `name` for None, because a composite IntFlag reports a joined
    name (`"Slewing|Parking"`) rather than None.
    """
    if isinstance(completion, Completion):
        return completion.value
    members = list(completion)
    if len(members) != 1:
        raise ValueError(f"a completion signal must name exactly one activity flag, got {completion!r}")
    return f"activity:{members[0].name}"


@dataclass(frozen=True)
class EndpointDeclaration:
    tier: Tier
    stability: Stability = Stability.STABLE
    #: HTTP verbs, for a route that is **generated** rather than written in an `api_router`
    #: body. Hand-registered routes keep their verb at the registration site, where the path
    #: is; `register_component_endpoints` has no such site, so the verb travels here.
    methods: tuple[str, ...] | None = None
    #: How the caller learns the operation finished. `None` means undeclared, which the
    #: contract check reports rather than silently treating as immediate.
    completion: Completion | IntFlag | None = None


class UndeclaredEndpointError(TypeError):
    """Raised at import when a route is registered on a method carrying no declaration."""


def endpoint(
    *,
    tier: Tier,
    stability: Stability = Stability.STABLE,
    factory: bool = False,
    methods: tuple[str, ...] | None = None,
    completion: Completion | IntFlag | None = None,
) -> Callable:
    """Declare a method as part of the HTTP surface, at its definition site.

    Keyword-only on purpose: `@endpoint(Tier.INTERFACE)` would read as a positional tier and
    invite a bare `@endpoint`, which is exactly the "one literal token finds the surface"
    property this exists to protect.

    `factory=True` declares a method that **builds and returns the handler** at registration
    time, rather than being the handler itself:

        @endpoint(tier=Tier.OPERATION, factory=True)
        def _spiral_new_path_endpoint(self):
            def endpoint_spiral_new_path(center_x: int | None = configured_x): ...
            return endpoint_spiral_new_path

        add_api_route(router, path, endpoint=self._spiral_new_path_endpoint())

    Some defaults cannot be written into a signature: a signature default is evaluated at
    import, long before `Config()` has loaded, so binding a unit's own configured values into
    the OpenAPI schema means building the handler in a closure after construction. That is a
    legitimate reason for a route to reach a function that is not a class attribute -- and it
    is also precisely the shape the `endpoint_` prefix used to hide, since a nested function
    carries no method name to scan.

    The declaration therefore rides on **both**: the factory keeps it, so the MRO scan in
    `declared_endpoints` still enumerates the surface; and every handler the factory produces
    is stamped with it, so `add_api_route` accepts what it is handed. One `@endpoint(` grep
    still finds this, which is why it is a flag on the existing decorator rather than a second
    decorator with a name of its own.
    """
    declaration = EndpointDeclaration(tier=tier, stability=stability, methods=methods, completion=completion)

    def mark(function: Callable) -> Callable:
        if not factory:
            setattr(function, MARKER, declaration)
            return function

        @functools.wraps(function)
        def build(*args: Any, **kwargs: Any) -> Callable:
            handler = function(*args, **kwargs)
            if not callable(handler):
                raise UndeclaredEndpointError(
                    f"'{getattr(function, '__qualname__', function)}' is declared "
                    f"factory=True but returned {type(handler).__name__}, not a handler."
                )
            setattr(handler, MARKER, declaration)
            return handler

        # `functools.wraps` has already copied the factory's `__dict__`; this is what puts the
        # declaration on the factory itself, so it is visible to the class-attribute scan.
        setattr(build, MARKER, declaration)
        return build

    return mark


def declaration_of(target: Any) -> EndpointDeclaration | None:
    """The declaration on `target`, or None. Accepts a function or a bound method.

    A bound method proxies attribute reads to its underlying function, so no unwrapping is
    needed -- but `functools.wraps` copies `__dict__`, which means a wrapper inherits the
    marker. That is deliberate and is what lets MAST_unit#34 stage 3 wrap the envelope at
    registration without the declaration being lost.
    """
    return getattr(target, MARKER, None)


def declared_endpoints(target: Any) -> dict[str, EndpointDeclaration]:
    """Every declared method on `target` (an instance or a class), by method name.

    Reads each class's `vars()` across the MRO, never `getattr` on the instance. A component
    is full of properties that touch hardware -- `connected` talks to an ASCOM driver -- so
    plain attribute access during a scan would connect a telescope as a side effect of asking
    a question about the class. `vars()` yields the descriptor objects themselves, so nothing
    is ever invoked. Reversed MRO order means a subclass declaration overrides a base one.
    """
    cls = target if isinstance(target, type) else type(target)
    found: dict[str, EndpointDeclaration] = {}
    for klass in reversed(cls.__mro__):
        for name, attribute in vars(klass).items():
            # A property's declaration would live on its getter; unwrap so a declared
            # property is not silently invisible here.
            if isinstance(attribute, property):
                attribute = attribute.fget
            declaration = declaration_of(attribute)
            if declaration is not None:
                found[name] = declaration
    return found


def _envelope(value: Any) -> CanonicalResponse:
    """Wrap a bare return value, and pass an existing envelope through untouched.

    The pass-through is not a convenience -- it is what makes double-wrapping impossible.
    MAST_unit#70 established the hazard concretely: `FullUnitStatus`'s fields are *typed as*
    the component status models, so an extra envelope nests inside the payload and breaks
    control / gui / SSE silently rather than loudly. 29 of the unit's handlers delegate to
    internal methods that already return a `CanonicalResponse`.

    It is also what lets the migration land component by component instead of as a flag day:
    a handler that still builds its own envelope keeps working unchanged.
    """
    return value if isinstance(value, CanonicalResponse) else CanonicalResponse(value=value)


def _as_canonical_error(name: str, exception: Exception) -> CanonicalResponse:
    """Turn an escaping exception into `errors`, after logging it with its traceback.

    `logger.exception` first, always. Uniformity at the API boundary is the goal; losing the
    diagnostics is not -- the tracebacks are what made MAST_unit#82, #85 and #86 findable.
    """
    logger.exception("%s: unhandled exception, returned as a canonical error", name)
    return CanonicalResponse(errors=[f"{type(exception).__name__}: {exception}"])


def enveloped(handler: Callable) -> Callable:
    """Wrap `handler` so it always answers a `CanonicalResponse` (invariant 4).

    Three mechanics, each of which breaks something if omitted:

    - **`functools.wraps` is mandatory.** FastAPI builds the OpenAPI schema and the parameter
      list from `inspect.signature`, which follows `__wrapped__`. Without it every routed
      handler would appear to take `*args, **kwargs` and the 17 handlers with real parameters
      would lose them.
    - **The return annotation is replaced on a fresh dict.** `functools.wraps` assigns the
      handler's own `__annotations__` object to the wrapper, so mutating it in place would
      edit the handler's annotations too.
    - **An async handler needs an async wrapper.** Three of the unit's handlers are
      `async def`; a sync wrapper would put the coroutine object into `value` and FastAPI
      would try to serialise it.
    """
    if inspect.iscoroutinefunction(handler):

        @functools.wraps(handler)
        async def wrapper(*args: Any, **kwargs: Any) -> CanonicalResponse:
            try:
                return _envelope(await handler(*args, **kwargs))
            except Exception as exception:  # noqa: BLE001 -- invariant 4: no exception escapes a handler
                return _as_canonical_error(getattr(handler, "__qualname__", "handler"), exception)

    else:

        @functools.wraps(handler)
        def wrapper(*args: Any, **kwargs: Any) -> CanonicalResponse:
            try:
                return _envelope(handler(*args, **kwargs))
            except Exception as exception:  # noqa: BLE001 -- invariant 4: no exception escapes a handler
                return _as_canonical_error(getattr(handler, "__qualname__", "handler"), exception)

    wrapper.__annotations__ = {**getattr(handler, "__annotations__", {}), "return": CanonicalResponse}
    return wrapper


def add_api_route(
    router: APIRouter,
    path: str,
    *,
    endpoint: Callable,
    methods: list[str] | None = None,
    **kwargs: Any,
) -> None:
    """Register a route, refusing any handler that has not declared itself.

    A drop-in for `router.add_api_route`, so a component's `api_router` keeps reading as a
    one-line-per-route index.

    The refusal is the point. A convention that is merely documented decays -- this one
    cannot be half-applied, because a missing declaration stops the process at import rather
    than shipping an untiered endpoint.

    **There is no `tags` parameter.** The tag is the tier, read from the declaration, so a
    route cannot be filed under one group and declared another. A caller still passing `tags=`
    fails at import rather than silently overriding it.

    Every handler is wrapped by `enveloped()` so it answers a `CanonicalResponse` and never
    a bare value, a `None` or an escaping exception (invariant 4, MAST_unit#34 stage 3). Doing
    it here rather than in 32 handler bodies is also what makes the parked HTTP-status-code
    decision (guidelines §4) tractable: it collapses ~70 error-construction sites to one.
    """
    declaration = declaration_of(endpoint)
    if declaration is None:
        name = getattr(endpoint, "__qualname__", repr(endpoint))
        raise UndeclaredEndpointError(
            f"{path}: '{name}' is routed but declares no tier. "
            f"Add @endpoint(tier=Tier.<TIER>) at its definition (MAST_unit#42 invariant 10)."
        )

    if "tags" in kwargs:
        raise TypeError(f"{path}: the tag is the tier, read from the declaration -- do not pass `tags`.")

    _register(router, path, endpoint=endpoint, declaration=declaration, methods=methods, **kwargs)


def _register(
    router: APIRouter,
    path: str,
    *,
    endpoint: Callable,
    declaration: EndpointDeclaration,
    methods: list[str] | None = None,
    **kwargs: Any,
) -> None:
    """Apply the declaration to a route and register it.

    Shared by `add_api_route`, which finds the declaration on the handler, and by
    `register_component_endpoints`, which already holds it -- the generated interface verbs are
    declared on the ABC, so the concrete override a component supplies carries no marker of its
    own and there is nothing on it to look up.
    """
    kwargs["tags"] = [TIER_TAGS[declaration.tier]]
    kwargs["openapi_extra"] = {
        **kwargs.get("openapi_extra", {}),
        "x-stability": TIER_STABILITY[declaration.tier],
    }
    if declaration.completion is not None:
        kwargs["openapi_extra"]["x-completion"] = completion_token(declaration.completion)

    # Additive: it puts `deprecated: true` on this operation and changes nothing else.
    if declaration.stability is Stability.DEPRECATED or declaration.tier is Tier.DEMO:
        kwargs.setdefault("deprecated", True)

    # Declared explicitly, not left to the wrapper's return annotation. `functools.wraps`
    # sets `__wrapped__`, and `inspect.signature` -- which is what FastAPI reads -- follows
    # it to the ORIGINAL handler. That transparency is exactly what preserves the parameter
    # list, and it is also why overriding `wrapper.__annotations__["return"]` has no effect
    # on the schema: FastAPI never looks at the wrapper's own annotations. Without this line
    # the 53 handlers that declare no return type would advertise an empty 200 schema.
    # `CanonicalResponse.value` is `Any | None`, so this validates the envelope without
    # filtering the payload inside it.
    kwargs.setdefault("response_model", CanonicalResponse)

    router.add_api_route(path, endpoint=enveloped(endpoint), methods=methods or ["GET"], **kwargs)


def register_component_endpoints(router: APIRouter, component: Any, base_path: str) -> None:
    """Register the component-interface verbs, generated from the `Component` ABC.

    A route is in the interface contract **iff it came from this generator** -- membership is
    provable by provenance rather than by five components each remembering to register the same
    four verbs the same way. They did not: the same verb was registered through a wrapper in one
    component and bare in another (MAST_unit#34, #40).

    The verb set is read from the ABC's own declarations, not hard-coded here, so a fifth
    lifecycle verb is one decorated method on `Component` and no edit to this function.

    Layer 2 only. `Unit` is a `Component` too, but its lifecycle verbs are the unit's
    orchestration surface and are declared `CONTRACT`; generating them from here would re-tier
    them to `INTERFACE` and move them in Swagger. They stay hand-registered.
    """
    from common.interfaces.components import Component

    declared = declared_endpoints(Component)
    if not declared:
        raise UndeclaredEndpointError("the Component ABC declares no interface verbs; the generated surface would be empty.")

    for name, declaration in declared.items():
        methods = list(declaration.methods or ("GET",))
        _register(
            router,
            f"{base_path}/{name}",
            endpoint=getattr(component, name),
            declaration=declaration,
            methods=methods,
        )
