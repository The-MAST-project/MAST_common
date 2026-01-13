import os
from typing import Literal


class Const:
    WEIZMANN_DOMAIN: str = "weizmann.ac.il"

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
