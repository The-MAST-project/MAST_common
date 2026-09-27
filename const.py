import os
from typing import Literal


class Const:
    BASE_SPEC_PATH: str = "/mast/api/v1/spec"
    BASE_UNIT_PATH: str = "/mast/api/v1/unit"
    BASE_CONTROL_PATH: str = "/mast/api/v1/control"
    BASE_DATA_PATH: str = BASE_CONTROL_PATH + "/data"

    # The supervisor's status endpoint. Deliberately a constant and not a `services` row
    # like every other port: its purpose is to describe a machine that cannot reach the
    # config database, which is exactly when a DB-derived port would not be known.
    BASE_SUPERVISOR_PATH: str = "/mast/api/v1/supervisor"
    SUPERVISOR_PORT: int = 8004

    PLATE_SOLVING_SHM_NAME: str = "PlateSolving_Image"

    SolvingPhase = Literal["sky", "spec"]  # acquisition phases that use plate solving
    CorrectionPhase = SolvingPhase  # acquisition phases that use corrections

    PlanFileNamePattern = "PLAN_*.toml"

    # Django server configuration
    DJANGO_HOST = os.getenv("DJANGO_HOST", "localhost")
    DJANGO_PORT = int(os.getenv("DJANGO_PORT", "8010"))
