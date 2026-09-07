# Scripting the API

**Commanding a MAST unit or the spectrograph from Python needs no MAST repository, no MAST
package, and no build step.** Both are ordinary HTTP services answering JSON. This page is
written to be self-contained: everything a script needs is here, including a boilerplate to
copy, so the file can be handed to someone (or to a coding agent) on its own.

If this file disagrees with the code, the code wins — and a service's own `/docs` page wins
over both. See *Keeping this current*.

## The two services

| service | base URL | what it drives |
|---|---|---|
| unit | `http://mastNN:8000/mast/api/v1/unit` | one telescope: mount, focuser, stage, covers, imager, guider |
| spec | `http://mast-ns-spec:8000/mast/api/v1/spec` | the shared spectrograph: DeepSpec, HighSpec, filter wheels, stages, shutter, chiller |

`mastNN` is always a physical unit — `mast01`, `mast02`, and so on. Port 8000 is the default a
service falls back to; the real port is a `services` entry in the configuration database, so
when a machine does not answer there, confirm the port before concluding the service is down.

Three things to know before the first call:

- **There is no authentication.** Anything that can reach the port can slew a telescope. That
  safety rests entirely on the closed VLAN the units live on, so run scripts from a machine on
  that network and do not proxy these ports anywhere.
- **It is plain HTTP.** No certificate, no TLS, nothing to configure.
- **A unit does not answer ping.** `GET .../unit/status` is the reachability test; a timeout
  there means unreachable, not necessarily down.

## Look before you write: Try it out

Every service serves interactive documentation at `/docs` — `http://mast01:8000/docs`. Find
the operation, press **Try it out**, fill in the fields, execute. The page then shows the exact
URL it called and the exact JSON that came back.

Do that once for every endpoint before scripting it. It settles the parameter names, the HTTP
verb and the response shape in a single step, and an endpoint that fails there will not work
from Python either. It is also the fastest way to see what a real `status` payload looks like,
which is the thing worth reading before writing any polling logic.

`http://mast01:8000/openapi.json` is the same surface, machine-readable. Fetch it to enumerate
what exists, or to generate a typed client (`openapi-python-client`, or
`datamodel-code-generator` for the models alone) into a scratch directory. It is the
authoritative list of operations, and no maintained second copy of it exists anywhere.

### Which endpoints to build on

The unit groups its operations by contract tier, in Swagger and as a machine-readable
`x-stability` on each operation:

| Swagger group | `x-stability` | use it in a script? |
|---|---|---|
| Unit orchestration (contract) | `contract` | Yes. This is the programmatic surface for observing |
| Component interface (contract) | `interface` | Yes. `startup` / `shutdown` / `abort` / `status`, uniform on every component |
| *Area* (operator) — `Mount (operator)`, `Imager (operator)`, … | `operator` | For driving hardware by hand. Expect it to change without notice |
| Demonstration (parked) | `demo` | No. Rendered struck through, and non-functional |

An exploratory script that lives on operator verbs is exactly what that tier is for — the
caution is against building something long-lived on one without knowing that is what you did.

**Both services publish this.** The spectrograph adopted the same contract module by module
in September 2026, so its operations carry the same tier groups, the same `x-stability` and the
same `x-completion` a unit's do. One contract, two services.

## Three rules that make a call work

**1. Arguments go in the query string, not in a JSON body.** Almost every parameter on both
services is declared as a query parameter, so `params={...}` is what a request needs and a JSON
body is silently ignored. The one exception on the unit is `execute_assignment`, which takes a
`UnitAssignment` as a JSON body.

**2. `GET` reads, `PUT` acts.** On both services, anything that moves hardware or changes
state is a `PUT`; `status`, the position getters and the wheel listing are `GET`. The
spectrograph served every route as `GET` until September 2026 and now answers **405** to those
calls, so an old script or a bookmarked URL fails loudly rather than quietly. Read the verb off
the `/docs` page rather than inferring it.

**3. Read `errors`, not the HTTP status code.** A unit answers one envelope and nothing else:

```json
{"api_version": "1.0", "value": {"activities_verbal": ["Slewing"]}, "errors": null}
```

Exactly one of the two fields is populated. Every handler on both services is wrapped at
registration, so a bare value, a `None` return and an escaping exception all arrive in this
shape: **HTTP 200 carrying a non-empty `errors` is the normal way a call fails.** Check
`errors` first and take `value` only when it is empty.

A body that is not an envelope means the URL is not one of these two services. The boilerplate
below says so rather than guessing, and a rejected parameter — a `422` whose body names the
offending field — is reported with that body rather than as a bare status code.

## Knowing when an operation has finished

A `PUT` that starts hardware moving normally returns as soon as the hardware **accepted** the
command, not when it has finished. Each unit operation declares how a caller learns it is
done, published per operation as `x-completion`:

| `x-completion` | what a script does |
|---|---|
| `immediate` | Finished when the response arrives |
| `blocking` | The response is withheld until the hardware is done. Allow a generous request timeout |
| `activity:<Flag>` | Returns at once. Poll `status` until `<Flag>` is no longer in `activities_verbal` |
| `notification:<channel>` | Reported on the notification stream rather than by polling |
| *absent* | Nobody has classified it. Do **not** read that as `immediate` — poll the matching activity and bound the wait |

`activities_verbal` is the list of flags currently set. Unit-level activities sit at the top of
the status payload; a component's sit under that component's key:

```
value["activities_verbal"]              ["StartingUp"]     unit-level
value["mount"]["activities_verbal"]     ["Slewing"]        mount
value["imager"]["activities_verbal"]    ["Exposing"]       imager
```

A component key can be `null` when that component failed to build — `status` is where a caller
learns which one, and why.

**Every wait gets a timeout.** A flag that never clears is a real failure mode, and an
unbounded poll turns it into a hung script instead of an error someone can read.

## Boilerplate

One dependency: `python -m pip install httpx`. (`requests` works the same way, and so does
`urllib.request` if a script must have no dependencies at all.)

```python
"""Minimal client for a MAST unit or the spec machine. Copy this and adapt it."""

from __future__ import annotations

import time
from typing import Any

import httpx

DEFAULT_PORT = 8000
UNIT_BASE_PATH = "/mast/api/v1/unit"
SPEC_BASE_PATH = "/mast/api/v1/spec"
SPEC_HOST = "mast-ns-spec"
REQUEST_TIMEOUT_SECONDS = 20.0
POLL_INTERVAL_SECONDS = 2.0
ERROR_BODY_CHARS = 400


class MastApiError(RuntimeError):
    """A MAST service reported errors, or could not be reached."""


class MastService:
    """One MAST HTTP service: a unit, or the spectrograph."""

    def __init__(self, host: str, base_path: str, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.base_url = f"http://{host}:{port}{base_path}"
        self._client = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False)

    @classmethod
    def unit(cls, name: str, port: int = DEFAULT_PORT) -> MastService:
        return cls(name, UNIT_BASE_PATH, port)

    @classmethod
    def spec(cls, host: str = SPEC_HOST, port: int = DEFAULT_PORT) -> MastService:
        return cls(host, SPEC_BASE_PATH, port)

    def get(self, method: str, **params: Any) -> Any:
        return self._request("GET", method, params)

    def put(self, method: str, **params: Any) -> Any:
        return self._request("PUT", method, params)

    def status(self) -> dict[str, Any]:
        return self.get("status")

    def wait_for(self, activity: str, *, timeout_seconds: float, component: str | None = None) -> None:
        """Block until `activity` is no longer reported, or raise once `timeout_seconds` passes."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            status = self.status()
            scope = status.get(component) if component else status
            if scope is None:
                raise MastApiError(f"{self.host}: no '{component}' in status — the component did not build")
            if activity not in (scope.get("activities_verbal") or []):
                return
            time.sleep(POLL_INTERVAL_SECONDS)
        raise MastApiError(f"{self.host}: '{activity}' still set after {timeout_seconds} s")

    def _request(self, verb: str, method: str, params: dict[str, Any]) -> Any:
        url = f"{self.base_url}/{method}"
        supplied = {name: value for name, value in params.items() if value is not None}
        try:
            response = self._client.request(verb, url, params=supplied)
        except httpx.HTTPError as error:
            raise MastApiError(f"{verb} {url}: {error}") from error

        if response.is_error:
            raise MastApiError(f"{verb} {url}: HTTP {response.status_code}: {response.text[:ERROR_BODY_CHARS]}")

        body = response.json()
        if not isinstance(body, dict) or "api_version" not in body:
            raise MastApiError(f"{verb} {url}: not a MAST response envelope: {body!r}")
        if body.get("errors"):
            raise MastApiError(f"{verb} {url}: {body['errors']}")
        return body.get("value")


def main() -> None:
    unit = MastService.unit("mast01")
    status = unit.status()
    print(f"operational={status['operational']} activities={status['activities_verbal']}")


if __name__ == "__main__":
    main()
```

## Two worked calls

**Slew the mount, then expose.** `mount/goto_ra_dec_j2000` declares
`completion=activity:Slewing`, so it returns on acceptance and the `Slewing` flag says when the
telescope arrived. Both coordinates are required, and each takes either sexagesimal or decimal
form (RA in hours, Dec in degrees):

```python
unit = MastService.unit("mast01")

unit.put("mount/goto_ra_dec_j2000", ra_j2000_hours="14:39:36", dec_j2000_degs="-60:50:02")
unit.wait_for("Slewing", component="mount", timeout_seconds=180)

unit.put("expose", exposure_seconds=5, repeats=3, gain=170)
unit.wait_for("Exposing", component="imager", timeout_seconds=120)
```

`expose` is an operator verb whose completion is **undeclared**, so that second wait is a
best-effort read of the imager's own activity rather than a declared signal — check the
frames landed rather than trusting the flag alone. Passing `ra_j2000_hours` and
`dec_j2000_degs` to `expose` makes it slew first; supply both or neither.

**Acquire a spectrum.** `spec_name` is one of `Deepspec` or `Highspec`:

```python
spec = MastService.spec()

spec.put("acquire", spec_name="Highspec", exposure_duration=10, lamp_on=False, number_of_exposures=1)
```

Spec activities appear in its own `status` under `activities_verbal`, so a wait there follows
the same shape with `component=None`.

## Before commanding hardware

- **This moves a real telescope.** The units sit under a rolling roof that is not remotely
  operable, so whether the sky is even reachable is not something a script can determine.
- **Nothing interlocks a script.** The safety client on a unit has no server behind it today,
  so no call is going to be refused on your behalf. The judgment is the operator's.
- **`abort` is the stop button** — `PUT .../unit/abort` for the unit, or a component's own
  `abort` for one subsystem. Know which one you would reach for before starting a slew.
- **Tell whoever is on site.** A unit answering HTTP is not the same as a unit that is free.

## Handing this file to a coding agent

A working script comes out of this page if the agent does these, in order:

1. Read this whole file first. It is the contract; do not infer the shape of the API from
   anything else in the repository.
2. Confirm the target answers before writing anything long:
   `curl -m 6 http://mast01:8000/mast/api/v1/unit/status`. If it times out, stop and report —
   the script is not the problem, the network path is.
3. Fetch `/openapi.json` from that same host and read the **real** parameter names, verbs and
   `x-completion` values for the endpoints the task needs. The examples on this page are
   illustrations, not a substitute for the live schema.
4. Prefer `contract` and `interface` operations. If the task needs an `operator` verb, use it
   and note at the call site that it is one. Never call a `demo` operation.
5. Copy the boilerplate rather than writing a client from scratch, and keep both of its
   safeguards: the `errors` check and the bounded wait.
6. Ask the human before the first call that moves hardware, and name the unit it will move.

## Keeping this current

The producer-side counterpart is `MAST_unit/docs/adding-an-endpoint.md` — how an endpoint is
declared, and what registration refuses. The vocabulary this page describes lives in one place
each: tiers, stability and completion in `common/endpoints.py`
([MAST_unit#42 MAST unit endpoint contract (tracking)](https://github.com/The-MAST-project/MAST_unit.2024-12-12/issues/42)),
the envelope in `common/canonical.py`, the base paths in `common/const.py`, and the activity
flags in `common/activities.py`.

`common/api.py` in this repository is the client the fleet's own services use. It is
deliberately **not** what a script should reach for: it resolves hosts and ports through the
Mongo-backed configuration layer, so importing it brings `pymongo` and a configuration file
that only a machine inside the fleet can satisfy. A script wants the twenty lines above
instead.
