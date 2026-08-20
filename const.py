import os
from enum import StrEnum
from typing import Literal


class UnitEndpoint(StrEnum):
    """The unit endpoint names a programmatic client depends on (MAST_unit#35).

    Both sides of the wire named these as independent string literals -- the unit built its
    paths by concatenation, the shared plan client re-typed them -- so a rename failed only at
    run time, as a 404, with nothing at import to catch the drift.

    **CONTRACT tier only.** Operator and diagnostic verbs have no programmatic client and stay
    literal; putting them here would imply a promise the contract does not make. The
    component-level lifecycle verbs are already single-sourced elsewhere: since MAST_unit#40
    they are generated from the `Component` ABC's own method names.
    """

    EXECUTE_ASSIGNMENT = "execute_assignment"
    STATUS = "status"
    ABORT = "abort"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"


class SpecEndpoint(StrEnum):
    """The spectrograph's equivalent -- a sibling enum per service, kept next to it.

    Deliberately **not** a copy of `UnitEndpoint`: the two hosts do not serve the same set,
    and one enum spanning both would let a caller name a route the target does not have. This
    lists what MAST_spec actually registers.

    Note what is absent: **`execute_assignment`**. The shared plan client calls the
    spectrograph with that name in three places, and MAST_spec serves no such route -- checked
    against all 52 of its registrations. Those call sites are therefore left as literals rather
    than given a member here, because inventing one would encode the bug as a contract.
    """

    STATUS = "status"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    POWERDOWN = "powerdown"
    ACQUIRE = "acquire"
    ABORT = "abort"


class Const:
    BASE_SPEC_PATH: str = "/mast/api/v1/spec"
    BASE_UNIT_PATH: str = "/mast/api/v1/unit"
    BASE_CONTROL_PATH: str = "/mast/api/v1/control"
    BASE_DATA_PATH: str = BASE_CONTROL_PATH + "/data"

    PLATE_SOLVING_SHM_NAME: str = "PlateSolving_Image"

    SolvingPhase = Literal["sky", "spec"]  # acquisition phases that use plate solving
    CorrectionPhase = SolvingPhase  # acquisition phases that use corrections

    PlanFileNamePattern = "PLAN_*.toml"

    # Django server configuration
    DJANGO_HOST = os.getenv("DJANGO_HOST", "localhost")
    DJANGO_PORT = int(os.getenv("DJANGO_PORT", "8010"))
