"""How far a unit or spec machine takes itself on boot: its operating mode, ``opmode``.

The modes are named for what the machine is being used for:

- ``development`` -- an engineer runs the app from VSCode, and it starts the machine
  immediately: ``start_lifespan`` calls ``startup()`` with no command. The machine may be
  driven through its component endpoints as well as its own.
- ``production`` -- the control machine's supervisor runs the app, and it waits for
  ``startup``. The machine is governed only through its own (unit-level) endpoints.

History: ``automatic`` / ``controlled`` until 2026-10-06, then ``operated`` / ``controlled``
until 2026-10-12. Those named who started the app; these name what the mode is *for*, which
is also what endpoint admission keys on (mast-claude-config ``plans/endpoint-admission-design.md``).
The two previous values are still accepted, with a WARNING (see ``OpMode._missing_``).

Resolved from one place, :func:`resolve_opmode`: the ``MAST_OPMODE`` environment
variable, then the machine's configuration (``UnitConfig.opmode`` / ``SpecsConfig.opmode``),
then ``development``.

This module imports nothing from ``common.config`` at module scope: the config package
imports pymongo, and a caller that only needs the environment must not pay for that.
Design: mast-claude-config ``plans/opmode-design.md``.

Changing the operating mode:

- The ``mast-supervisor`` can switch the opmode between ``development`` and ``production`` in the
  machine's configuration. It then restarts the app, which comes up in the new mode.
- The mode is picked up only at startup and is not persisted in the app's state: the app does
  not change its mode on the fly.

"""

# How-to: https://github.com/The-MAST-project/MAST/wiki/Running-a-unit

from __future__ import annotations

import os
from enum import StrEnum

from common.mast_logging import get_logger

logger = get_logger(__name__)


#: The values these modes had before 2026-10-12. Still stored in config documents and
#: `MAST_OPMODE` settings written before then; drop once none remain.
_LEGACY_OPMODES = {"operated": "development", "controlled": "production"}


class OpMode(StrEnum):
    DEVELOPMENT = "development"  # an engineer runs the app from VSCode; it starts the machine at once
    PRODUCTION = "production"  # the supervisor runs the app; it waits for ``startup``

    # Deprecated member names: Enum aliases (same value), so `OpMode.CONTROLLED is
    # OpMode.PRODUCTION` and iterating `OpMode` still yields two modes. Same lifetime as
    # `OpmodeBase.is_operated` / `is_controlled`.
    OPERATED = "development"
    CONTROLLED = "production"

    @classmethod
    def _missing_(cls, value: object) -> OpMode | None:
        """Accept a pre-2026-10-12 value, with a WARNING naming its replacement.

        Every machine reads one shared config DB, so the stored values cannot change at the
        same instant as the code on every machine. New code accepting the old names is what
        lets the code roll out first and the documents change after. Pydantic validation goes
        through here too, so a `UnitConfig` holding `controlled` still loads.
        """
        new = _LEGACY_OPMODES.get(str(value).strip().lower())
        if new is None:
            return None
        logger.warning(f"opmode {value!r} is the pre-2026-10-12 name; read as {new!r} -- update the setting")
        return cls(new)


class OpState(StrEnum):
    """Where a unit or spec machine is in its lifecycle: the lifecycle state it has REACHED.

    ``INITIALIZING`` -> ``INITIALIZED`` -> ``STARTED_UP`` <-> ``SHUT_DOWN``:

    - ``INITIALIZING`` -- from the start of the top component's ``__init__``. Never seen over
      HTTP: uvicorn opens its port only once the lifespan's startup half has returned.
    - ``INITIALIZED`` -- set at the end of ``start_lifespan`` under ``production``; awaiting the
      first ``startup``. Entered once per process.
    - ``STARTED_UP`` -- set when the startup sequence **completes** (the top component's
      ``StartingUp`` activity ends), not on receipt of ``startup``.
    - ``SHUT_DOWN`` -- set when the shutdown sequence **completes** (``ShuttingDown`` ends).

    A transition in progress is the ``StartingUp`` / ``ShuttingDown`` activity, not a state:
    during a startup the opstate is still ``INITIALIZED`` or ``SHUT_DOWN``, during a shutdown
    still ``STARTED_UP``. Only the most recent lifecycle command completes into a state, so a
    shutdown received mid-startup ends in ``SHUT_DOWN`` even if the startup finishes later.

    (Until 2026-10-13 the opstate was the last command *received*: ``RUNNING`` and ``SHUTDOWN``,
    both set on receipt.)

    Health is not part of it: that is ``operational`` / ``why_not_operational``. A startup in
    which a component failed still completes into ``STARTED_UP``. Only the top component (the
    unit or the spec) carries an ``opstate``; its components do not. Not persisted, but
    reported to the control machine. Design: opmode-design section 4.
    """

    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    STARTED_UP = "startedup"
    SHUT_DOWN = "shutdown"


OPMODE_ENV = "MAST_OPMODE"
DEFAULT_OPMODE = OpMode.DEVELOPMENT


def opmode_from_env() -> OpMode | None:
    """The mode named by ``MAST_OPMODE``, or ``None`` when it is unset.

    An unrecognized value raises rather than falling back: ``MAST_OPMODE=prodution``
    silently becoming ``development`` would have a unit run its startup while its supervisor
    believes it is waiting. A pre-2026-10-12 value is accepted, with a WARNING.
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
    return resolve_opmode_with_source()[0]


def resolve_opmode_with_source() -> tuple[OpMode, str]:
    """:func:`resolve_opmode`, plus a short phrase naming where the mode came from.

    The phrase is for the log: ``MAST_OPMODE`` left set in a shell is otherwise
    indistinguishable from the configuration saying the same thing.
    """
    from_env = opmode_from_env()
    if from_env is not None:
        return from_env, OPMODE_ENV

    try:
        from common.config import Config
        from common.config.local import load_local_config

        match load_local_config().machine_role:
            case "unit":
                unit = Config().get_unit()
                if unit is None:
                    logger.warning(f"resolve_opmode: no configuration for this unit; using {DEFAULT_OPMODE}")
                    return DEFAULT_OPMODE, "default, no configuration for this unit"
                return unit.opmode, "unit configuration"
            case "spec":
                return Config().get_specs().opmode, "specs configuration"
            case role:
                return DEFAULT_OPMODE, f"default, machine_role {role}"
    except Exception:  # noqa: BLE001 -- an unreadable configuration is a reported, defaulted state
        logger.warning(f"resolve_opmode: configuration unreadable; using {DEFAULT_OPMODE}", exc_info=True)
        return DEFAULT_OPMODE, "default, configuration unreadable"


class OpmodeBase:
    """A base class for the top component (unit or spec): its operating mode and lifecycle state.

    Read by the top component only. Components do not branch on the mode or the state: the top
    component decides, in ``start_lifespan``, whether to start at once (opmode-design 4a).
    """

    def __init__(self) -> None:
        self._opmode: OpMode | None = None
        self._opmode_source: str | None = None
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
            self._opmode, self._opmode_source = resolve_opmode_with_source()
        return self._opmode

    @property
    def opmode_source(self) -> str:
        """Where :attr:`opmode` came from (see :func:`resolve_opmode_with_source`).

        ``preset`` when ``_opmode`` was assigned directly, as tests do, bypassing resolution.
        """
        _ = self.opmode
        return self._opmode_source or "preset"

    @property
    def is_development(self) -> bool:
        """True when the machine is in ``development`` mode, false when ``production``."""
        return self.opmode == OpMode.DEVELOPMENT

    @property
    def is_production(self) -> bool:
        """True when the machine is in ``production`` mode, false when ``development``."""
        return not self.is_development

    # The pre-2026-10-12 names. A `common` pull updates every consumer on a machine at once
    # (CLAUDE.md), and MAST_spec -- not converted in the same change -- may still read these.
    # Drop once no consumer does.

    @property
    def is_operated(self) -> bool:
        """Deprecated: :attr:`is_development`."""
        return self.is_development

    @property
    def is_controlled(self) -> bool:
        """Deprecated: :attr:`is_production`."""
        return self.is_production
