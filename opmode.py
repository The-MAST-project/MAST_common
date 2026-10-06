"""How far a unit or spec machine takes itself on boot: its operating mode, ``opmode``.

The modes are named for who is in charge of the machine:

- ``operated`` -- a person (the operator) runs the app from VSCode, and it starts the
  machine immediately: ``start_lifespan`` calls ``startup()`` with no command.
- ``controlled`` -- the control machine's supervisor runs the app, and it waits for
  ``startup``.

(``operated`` was ``automatic`` until 2026-10-06. ``manual`` was rejected: it reads as "nothing
happens until someone commands it", the opposite of this mode.)

Resolved from one place, :func:`resolve_opmode`: the ``MAST_OPMODE`` environment
variable, then the machine's configuration (``UnitConfig.opmode`` / ``SpecsConfig.opmode``),
then ``operated``.

This module imports nothing from ``common.config`` at module scope: the config package
imports pymongo, and a caller that only needs the environment must not pay for that.
Design: mast-claude-config ``plans/opmode-design.md``.

Changing the operating mode:

- The ``mast-supervisor`` can switch the opmode between ``operated`` and ``controlled`` in the
  machine's configuration. It then restarts the app, which comes up in the new mode.
- The mode is picked up only at startup and is not persisted in the app's state: the app does
  not change its mode on the fly.

"""

from __future__ import annotations

import os
from enum import StrEnum

from common.mast_logging import get_logger

logger = get_logger(__name__)


class OpMode(StrEnum):
    OPERATED = "operated"  # a person runs the app from VSCode; it starts the machine at once
    CONTROLLED = "controlled"  # the supervisor runs the app; it waits for ``startup``


class OpState(StrEnum):
    """Where a unit or spec machine is in its lifecycle: the last lifecycle command it received.

    ``INITIALIZING`` -> ``INITIALIZED`` -> ``RUNNING`` <-> ``SHUTDOWN``:

    - ``INITIALIZING`` -- from the start of the top component's ``__init__``. Never seen over
      HTTP: uvicorn opens its port only once the lifespan's startup half has returned.
    - ``INITIALIZED`` -- set at the end of ``start_lifespan`` under ``controlled``; awaiting the
      first ``startup``. Entered once per process.
    - ``RUNNING`` -- set on receipt of ``startup`` (under ``operated``, ``start_lifespan`` sends it).
    - ``SHUTDOWN`` -- set on receipt of ``shutdown``; a repeat ``shutdown`` leaves it there.

    Health is not part of it: that is ``operational`` / ``why_not_operational``. Only the top
    component (the unit or the spec) carries an ``opstate``; its components do not. Not
    persisted, but reported to the control machine. Design: opmode-design section 4.
    """

    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    RUNNING = "running"
    SHUTDOWN = "shutdown"


OPMODE_ENV = "MAST_OPMODE"
DEFAULT_OPMODE = OpMode.OPERATED


def opmode_from_env() -> OpMode | None:
    """The mode named by ``MAST_OPMODE``, or ``None`` when it is unset.

    An unrecognized value raises rather than falling back: ``MAST_OPMODE=controled``
    silently becoming ``operated`` would have a unit run its startup while its supervisor
    believes it is waiting.
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


class OpmodeBase:
    """A base class for the top component (unit or spec): its operating mode and lifecycle state.

    Read by the top component only. Components do not branch on the mode or the state: the top
    component decides, in ``start_lifespan``, whether to start at once (opmode-design 4a).
    """

    def __init__(self) -> None:
        self._opmode: OpMode | None = None
        self._opstate: OpState = OpState.INITIALIZING

    @property
    def opstate(self) -> OpState:
        """The last lifecycle command received (see :class:`OpState`). Read-only from outside."""
        return self._opstate

    def _set_opstate(self, new: OpState) -> None:
        """The one writer, called by the top component on receipt of a lifecycle command.

        Logged on change, so a night's log shows every transition.
        """
        if new != self._opstate:
            logger.info(f"opstate {self._opstate} -> {new}")
        self._opstate = new

    @property
    def opmode(self) -> OpMode:
        """The effective operating mode, cached after first access."""
        if self._opmode is None:
            self._opmode = resolve_opmode()
        return self._opmode

    @property
    def is_operated(self) -> bool:
        """True when the machine is in ``operated`` mode, false when ``controlled``."""
        return self.opmode == OpMode.OPERATED

    @property
    def is_controlled(self) -> bool:
        """True when the machine is in ``controlled`` mode, false when ``operated``."""
        return not self.is_operated
