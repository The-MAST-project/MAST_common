"""How far a unit or spec machine takes itself on boot: its operating mode, ``opmode``.

- ``automatic`` -- the machine is set up for the app, and whoever runs the app gets a
  process that proceeds straight to ``startup()``. The operators run it from VSCode.
- ``controlled`` -- the app comes up in standby and waits for the control machine.

Resolved from one place, :func:`resolve_opmode`: the ``MAST_OPMODE`` environment
variable, then the machine's configuration (``UnitConfig.opmode`` / ``SpecsConfig.opmode``),
then ``automatic``.

This module imports nothing from ``common.config`` at module scope: the config package
imports pymongo, and a caller that only needs the environment must not pay for that.
Design: mast-claude-config ``plans/opmode-design.md``.
"""

from __future__ import annotations

import os
from enum import StrEnum

from common.mast_logging import get_logger

logger = get_logger(__name__)


class OpMode(StrEnum):
    AUTOMATIC = "automatic"
    CONTROLLED = "controlled"


OPMODE_ENV = "MAST_OPMODE"
DEFAULT_OPMODE = OpMode.AUTOMATIC


def opmode_from_env() -> OpMode | None:
    """The mode named by ``MAST_OPMODE``, or ``None`` when it is unset.

    An unrecognized value raises rather than falling back: ``MAST_OPMODE=controled``
    silently becoming ``automatic`` would have a unit run its startup while its operator
    believes it is standing by.
    """
    raw = os.environ.get(OPMODE_ENV)
    if raw is None:
        return None
    try:
        return OpMode(raw.strip().lower())
    except ValueError:
        legal = ", ".join(mode.value for mode in OpMode)
        raise ValueError(f"{OPMODE_ENV}={raw!r} is invalid; expected one of {legal}") from None


def resolve_opmode() -> OpMode:
    """The effective mode: the environment, then this machine's configuration, then the default.

    A configuration that cannot be read yields the default with a WARNING, never an
    exception -- the mode is asked for early, and the caller must still come up.
    """
    from_env = opmode_from_env()
    if from_env is not None:
        return from_env

    try:
        from common.config import Config
        from common.config.local import load_local_config

        match load_local_config().machine_role:
            case "unit":
                unit = Config().get_unit()
                if unit is None:
                    logger.warning(f"resolve_opmode: no configuration for this unit; using {DEFAULT_OPMODE}")
                    return DEFAULT_OPMODE
                return unit.opmode
            case "spec":
                return Config().get_specs().opmode
            case _:
                return DEFAULT_OPMODE
    except Exception:  # noqa: BLE001 -- an unreadable configuration is a reported, defaulted state
        logger.warning(f"resolve_opmode: configuration unreadable; using {DEFAULT_OPMODE}", exc_info=True)
        return DEFAULT_OPMODE
