"""Configuration for the MAST supervisor (``mast-supervisor``, in MAST_supervision).

Carried as ``UnitConfig.supervisor`` and ``SpecsConfig.supervisor``. **Every field is
defaulted**: ``Config.get_unit()`` merges ``units.common`` with the per-unit document, and
a required field absent from ``common`` would raise for every unit in the fleet at once.

Deliberately absent: the ports of the supervised programs (PWI4, PHD2, ps3cli) and the
supervisor's own. A port configurable here alone could disagree with the one the app
connects to, which is worse than a constant.

Design: mast-claude-config ``plans/supervisor-design.md`` §10.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SupervisionMode(StrEnum):
    """What the supervisor does with one program."""

    SUPERVISED = "supervised"  # spawn if absent, probe, restart when unhealthy
    OBSERVED = "observed"  # probe and report only; never spawn, restart or kill
    DISABLED = "disabled"  # not probed, not reported


class ManagedProcessConfig(BaseModel):
    """One program the supervisor owns or watches."""

    mode: SupervisionMode = SupervisionMode.SUPERVISED
    exe: str | None = None  # None: located at runtime
    args: list[str] = Field(default_factory=list)
    probe_interval_seconds: float = 15.0
    unhealthy_threshold: int = 3  # consecutive failed probes before a restart
    restart_backoff_seconds: float = 5.0
    restart_backoff_cap_seconds: float = 120.0
    crash_loop_restarts: int = 5  # this many restarts inside the window stops supervision
    crash_loop_window_seconds: float = 600.0


class ResourcesConfig(BaseModel):
    """The machine preconditions waited on before the supervisor proceeds."""

    wait_budget_seconds: float = 300.0  # then proceed degraded, and keep probing
    probe_interval_seconds: float = 5.0
    index_dir: str = "D:/mast-indexes"
    index_series: int = 5206
    index_count: int = 47


class AppConfig(BaseModel):
    """The unit or spec app the supervisor launches or watches."""

    probe_interval_seconds: float = 15.0
    stop_grace_seconds: float = 10.0


class VSCodeConfig(BaseModel):
    """The editor launched once under ``automatic``."""

    exe: str | None = None  # None: located at runtime
    workspace: str = "mast-{role}.code-workspace"  # under <top>; {role} is the machine role


class GuiConfig(BaseModel):
    """The supervisor's window and its rolling log."""

    log_buffer_records: int = 10000
    drain_interval_ms: int = 100
    drain_max_records_per_tick: int = 200
    log_pane_max_lines: int = 2000
    log_level: str = "INFO"


class SupervisorConfig(BaseModel):
    """Configuration for ``mast-supervisor``. The default supervises nothing."""

    processes: dict[str, ManagedProcessConfig] = Field(default_factory=dict)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    app: AppConfig = Field(default_factory=AppConfig)
    vscode: VSCodeConfig = Field(default_factory=VSCodeConfig)
    gui: GuiConfig = Field(default_factory=GuiConfig)
    heartbeat_interval_seconds: float = 300.0
    stop_grace_seconds: float = 10.0

    @classmethod
    def for_unit(cls) -> SupervisorConfig:
        """A unit's default: PWI4, PHD2 and ps3cli.

        PHD2 is observed rather than supervised until MAST_unit stops spawning phd2.exe
        itself from ``PHD2Connector.__init__``; two spawners would fight over one PHD2.
        """
        return cls(
            processes={
                "pwi4": ManagedProcessConfig(probe_interval_seconds=10.0),
                "phd2": ManagedProcessConfig(mode=SupervisionMode.OBSERVED, probe_interval_seconds=15.0),
                "ps3cli": ManagedProcessConfig(probe_interval_seconds=30.0),
            }
        )
