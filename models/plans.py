from __future__ import annotations

import datetime
import json
import os
import shutil
import socket
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
import ulid
from pydantic import BaseModel, Field, ValidationError, field_serializer, field_validator, model_validator
from pydantic.config import ConfigDict

from common.activities import Activities
from common.mast_logging import get_logger
from common.models.constraints import ConstraintsModel
from common.models.events import EventModel
from common.models.spectrographs import SpectrographModel
from common.models.targets import Target
from common.parsers import ANY_UNITS, expand_units, is_unit_specifier

if TYPE_CHECKING:
    from common.models.assignments import Manifest

logger = get_logger(__name__)

#: Seconds a unit gets to go from "acquire" to guiding: a plan's default, and its upper
#: bound. Also used by MAST_control's new-plan template and its multi-unit acquisition.
DEFAULT_TIMEOUT_TO_GUIDING: float = 600


class PlanStage(StrEnum):
    """The stages whose products an execution links into its products folder, by name
    (plans/control_acquisition_design.md 5.10)."""

    Autofocus = "Autofocus"  # focus_on_star
    Acquisition = "Acquisition"  # start_acquisition_and_guiding, its plate solves included
    Spec = "Spec"  # the spectrograph's exposures


#: The files a producer driven by a plan, or by a batch, writes into each products folder it
#: makes, holding that plan's or batch's ULID: the folder says what it belongs to wherever it
#: is found, without control's links (plans/control_acquisition_design.md 5.10).
PLAN_ID_FILE = "plan_id"
BATCH_ID_FILE = "batch_id"


def write_plan_id_file(folder: str | os.PathLike | None, plan_ulid: object | None) -> str | None:
    """Write `PLAN_ID_FILE` into ``folder`` and send it on to the share; its path, or None."""
    return _write_id_file(folder, PLAN_ID_FILE, plan_ulid)


def write_batch_id_file(folder: str | os.PathLike | None, batch_ulid: object | None) -> str | None:
    """Write `BATCH_ID_FILE` into ``folder`` and send it on to the share; its path, or None."""
    return _write_id_file(folder, BATCH_ID_FILE, batch_ulid)


def _write_id_file(folder: str | os.PathLike | None, name: str, ulid: object | None) -> str | None:
    """Written under `protect`, so it is a product -- it travels to the share with the rest
    and is never discarded with the folder as scratch -- and handed to the mover. Nothing
    without a folder (an imager with no files) or a ULID (an operator's call). Never raises:
    the file must not fail the operation it labels. ``ulid`` as a string or a `ULID` -- a
    `Batch` carries the latter.
    """
    text = "" if ulid is None else str(ulid)
    if folder is None or not text:
        return None
    from common.filer import Filer, MoveGuardian

    path = os.path.join(os.fspath(folder), name)
    try:
        os.makedirs(os.fspath(folder), exist_ok=True)
        with MoveGuardian().protect(path), open(path, "w", encoding="utf-8") as f:
            f.write(f"{text}\n")
        Filer().move_ram_to_shared(path)
    except OSError as e:
        logger.warning(f"{name} {text}: could not write '{path}': {e}")
        return None
    return path


@dataclass(frozen=True)
class PlanProducts:
    """One execution's products folder: `plans/products/PLAN_<ulid>/<YYYY-MM-DD_HH-MM-SS>/`,
    with a folder per stage and per producer, into which control links what the producers
    report (plans/control_acquisition_design.md 5.10). Control's: it runs where the share is
    a local disk, and only it can make links there."""

    plan_ulid: str
    execution: str
    root: str

    @classmethod
    def start(cls, plan_ulid: str, now: datetime.datetime | None = None, base: str | None = None) -> PlanProducts:
        """Name the execution by its UTC start and create its folder. Two starts in one
        second: the second gets `_2`, and so on."""
        from common.paths import PathMaker

        when = now or datetime.datetime.now(datetime.UTC)
        stamp = when.astimezone(datetime.UTC).strftime("%Y-%m-%d_%H-%M-%S")
        plan_folder = Path(base or PathMaker.make_plan_products_folder()) / f"PLAN_{plan_ulid}"
        plan_folder.mkdir(parents=True, exist_ok=True)
        name, n = stamp, 2
        while True:
            try:
                (plan_folder / name).mkdir()
                break
            except FileExistsError:
                name, n = f"{stamp}_{n}", n + 1
        return cls(plan_ulid=plan_ulid, execution=name, root=(plan_folder / name).as_posix())

    def folder_for(self, stage: PlanStage, producer: str) -> Path:
        return Path(self.root) / stage.value / producer

    def link(self, stage: PlanStage, producer: str, target: str | os.PathLike) -> Path:
        """A relative link to ``target``, named after it, in `folder_for(stage, producer)`.

        Relative, so it resolves on control and through the share alike. The target may not
        exist yet -- products reach the share after the RAM-to-share move -- and that is not
        an error. Idempotent: an existing link is left as it is.
        """
        folder = self.folder_for(stage, producer)
        folder.mkdir(parents=True, exist_ok=True)
        target = Path(target)
        link = folder / target.name
        if link.is_symlink() or link.exists():
            return link
        os.symlink(os.path.relpath(target, folder), link, target_is_directory=True)
        return link


def _without_empty_tables(value):
    """``value`` with every dict that ends up with no keys removed, recursively. Lists stay,
    empty or not: `scheduled_units = []` says "not scheduled"."""
    if isinstance(value, dict):
        pruned = {key: _without_empty_tables(item) for key, item in value.items()}
        return {key: item for key, item in pruned.items() if not (isinstance(item, dict) and not item)}
    if isinstance(value, list):
        return [_without_empty_tables(item) for item in value]
    return value


#: Never written to a plan file: where it was loaded from is not part of the plan.
_NOT_IN_THE_FILE = {"full_path", "toml_file"}


def plan_to_toml(plan: Plan) -> str:
    """The only way a plan reaches a file (plans/control_acquisition_design.md 3.4): no
    `None`s, no empty tables (`[target.science]` with nothing set), meaningful defaults such
    as `merit = 1` kept -- which `exclude_defaults` would drop."""
    data = plan.model_dump(mode="json", exclude_none=True, exclude=_NOT_IN_THE_FILE)
    return tomlkit.dumps(_without_empty_tables(data))


class Plan(BaseModel, Activities):
    target: Target
    model_config = ConfigDict(
        extra="allow",
        arbitrary_types_allowed=True,
    )

    ulid: str | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    full_path: Path | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    owner: str | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Owner",
                "widget": "user",
                "editable": False,
                "summary": True,
            },
            "searchable": "exact",
        },
    )
    mockup: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Mockup",
                "widget": "checkbox",
                "hidden": True,
                "tooltip": "If true, the plan will not be executed but only go through "
                "the scheduling phase (for testing and debugging)",
            },
            "searchable": True,
        },
    )
    merit: int | None = Field(
        default=1,
        json_schema_extra={
            "ui": {
                "label": "Merit",
                "widget": "select",
                "options": list(range(1, 11)),
                "summary": True,
            },
            "searchable": "exact",
        },
    )
    timeout_to_guiding: float | None = Field(
        default=DEFAULT_TIMEOUT_TO_GUIDING,
        gt=0,
        le=DEFAULT_TIMEOUT_TO_GUIDING,
        json_schema_extra={
            "ui": {
                "label": "Timeout to Guiding",
                "widget": "number",
                "unit": "seconds",
                "required_capabilities": ["can_manage_plans"],
            },
        },
    )
    autofocus: bool | None = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Autofocus",
                "widget": "checkbox",
                "tooltip": "Perform autofocus before acquisition",
            },
            "searchable": True,
        },
    )
    too: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Target of Opportunity",
                "widget": "checkbox",
                "summary": True,
                "required_capabilities": ["can_manage_plans"],
            },
            "searchable": True,
        },
    )
    approved: bool = Field(
        default=False,
        json_schema_extra={
            "ui": {
                "label": "Approved",
                "widget": "checkbox",
                "editable": False,
                "required_capabilities": ["can_manage_plans"],
                "hidden": True,
            },
            "searchable": True,
        },
    )
    spec: SpectrographModel | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Spectrograph",
            }
        },
    )
    production: bool | None = Field(
        default=True,
        json_schema_extra={
            "ui": {
                "label": "Production",
                "widget": "checkbox",
                "required_capabilities": ["can_manage_plans"],
                "tooltip": "Disable to relax availability checks (testing only)",
                "hidden": True,
            }
        },
    )
    events: list[EventModel] | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "hidden": True,
            }
        },
    )
    constraints: ConstraintsModel | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Constraints",
            }
        },
    )
    quorum: int = Field(
        default=1,
        json_schema_extra={
            "ui": {
                "label": "Quorum",
                "widget": "number",
                "tooltip": "Fewest units to continue: the plan does not start with fewer, and is aborted when fewer remain",
                "required_capabilities": ["can_manage_plans"],
                "section": "Units",
            }
        },
    )
    requested_units: list[str] = Field(
        min_length=1,
        json_schema_extra={
            "ui": {
                "label": "Requested",
                "tooltip": "Unit specifiers (mast01, ns:1,3,5, ns:10-17), or <b>any</b>",
                "required": True,
                "section": {"label": "Units"},
            }
        },
    )
    scheduled_units: list[str] = Field(
        default_factory=list,
        json_schema_extra={
            "ui": {
                "label": "Scheduled",
                "editable": False,
                "required_capabilities": ["can_manage_plans"],
                "tooltip": "Units chosen by the scheduler; empty until it chooses",
                "section": "Units",
            }
        },
    )

    @field_validator("requested_units", mode="before")
    @classmethod
    def _a_string_is_one_entry(cls, v):
        """People write `"any"`, or one unit; code always sees a list."""
        return [v] if isinstance(v, str) else v

    @field_validator("requested_units")
    @classmethod
    def _specifiers_or_any(cls, v: list[str]) -> list[str]:
        if ANY_UNITS in v and v != [ANY_UNITS]:
            raise ValueError(f"'{ANY_UNITS}' can only be the sole entry of requested_units, got {v}")
        bad = [u for u in v if u != ANY_UNITS and not is_unit_specifier(u)]
        if bad:
            raise ValueError(f"not unit specifiers: {bad}")
        return v

    @field_serializer("requested_units")
    def _any_is_a_word(self, v: list[str]):
        return ANY_UNITS if v == [ANY_UNITS] else v

    @model_validator(mode="after")
    def _quorum_fits_the_named_units(self) -> Plan:
        if self.quorum < 1:
            raise ValueError(f"quorum must be at least 1, got {self.quorum}")
        # Specifiers can name several units each (ns:1-5); the count is known only once
        # they are expanded, by whoever runs the plan (5.6). One entry is at least one unit.
        one_unit_each = self.requested_units != [ANY_UNITS] and all(
            "," not in u and "-" not in u.split(":")[-1] for u in self.requested_units
        )
        if one_unit_each and self.quorum > len(self.requested_units):
            raise ValueError(f"quorum {self.quorum} is more than the {len(self.requested_units)} requested units")
        return self

    def requested_unit_names(self, sites=None, local_site=None) -> list[str] | None:
        """The requested units as hostnames (``mast01``), or None for `"any"`.

        Expanded, not filtered: whoever runs the plan says why a unit is not used (5.6).
        `sites` / `local_site` default to the configuration's. Raises ValueError for a
        specifier that names no unit.
        """
        if self.requested_units == [ANY_UNITS]:
            return None
        if sites is None or local_site is None:
            from common.config import Config

            sites = Config().sites if sites is None else sites
            local_site = Config().local_site if local_site is None else local_site
        return expand_units(self.requested_units, sites, local_site)

    @property
    def remote_spec_assignment(self) -> Manifest | None:
        from common.config import Config
        from common.config.local import load_local_config
        from common.models.assignments import (
            Initiator,
            Manifest,
            SpectrographAssignment,
        )

        local_site = Config().local_site
        assert local_site is not None
        spec_hostname = local_site.spec_host
        if spec_hostname is None:
            return
        fqdn = f"{spec_hostname}.{load_local_config().domain}"
        try:
            ipaddr = socket.gethostbyname(spec_hostname)
        except socket.gaierror:
            ipaddr = None

        if not self.spec:
            logger.error("cannot create a spectrograph model, aborting!")
            return None
        if not self.spec.instrument:
            logger.error("spectrograph model has no instrument, aborting!")
            return None

        initiator = Initiator.local_machine()
        try:
            spec = SpectrographAssignment(
                instrument=self.spec.instrument,
                initiator=initiator,
                plan=self,
                spec=self.spec,
            )
        except ValidationError as e:
            for err in e.errors():
                logger.error(f"ERR:\n  {err}")
            raise

        return Manifest(hostname=spec_hostname, fqdn=fqdn, ipaddr=ipaddr, assignment=spec)

    @classmethod
    def from_toml_file(cls, toml_file: str):
        """
        Loads and canonicalizes a TOML model from a plan file and ensures it has required fields.

        If the filename complies with the expected naming convention for plan files (PLAN_*.toml),
         the ULID is extracted from the filename and set as the plan's ulid (if not already set or not he same).
        If the filename does not comply with the expected naming convention:
        - if it contains a ulid field, it is used and the file is copied to the same directory with the correct name.
        - if it does not contain a ulid field, a new ULID is generated and the file is
          copied to the same directory with the correct name.

        Args:
            toml_file: Path to a TOML format plan definition file.


        Returns:
            Plan: The loaded and validated plan model.

        Raises:
            FileNotFoundError: If the TOML file does not exist
            tomlkit.exceptions.TOMLKitError: If the TOML file is invalid
            OSError: If there are file read/write errors
        """
        toml_path = Path(toml_file)
        if not toml_path.exists():
            raise FileNotFoundError(f"Plan file not found: {toml_file}") from None

        real_path = toml_path.resolve()
        folder = real_path.parent
        basename = toml_path.name
        if len(basename) == 36 and basename.startswith("PLAN_") and basename.endswith(".toml"):
            # Filename complies with PLAN_*.toml, extract ULID
            ulid_str = basename[5:-5]
            ulid_from_basename = ulid.ULID.from_str(ulid_str)
        else:
            # Filename does not comply, generate and enforce new ULID
            ulid_from_basename = ulid.ULID()
            basename = f"PLAN_{ulid_from_basename!s}.toml"
            new_path = folder / basename
            if not new_path.exists():
                shutil.copy(toml_file, str(new_path))
                logger.warning(f"Copied plan file '{toml_file}' to comply with naming convention: '{new_path}'")
            real_path = new_path.resolve()

        try:
            with open(real_path) as fp:
                toml_doc = tomlkit.load(fp)
        except Exception as e:
            import traceback

            traceback.print_exc()
            raise Exception(f"Invalid TOML file {toml_file}: {e!s}") from e

        if "ulid" not in toml_doc or toml_doc["ulid"] != str(ulid_from_basename):
            toml_doc["ulid"] = str(ulid_from_basename)
            # Rewrite the TOML file with the correct ULID
            try:
                with open(real_path, "w") as fp:
                    fp.write(tomlkit.dumps(toml_doc))
                logger.warning(f"Updated ULID in plan file '{real_path}' to '{ulid_from_basename}'")
            except Exception as e:
                import traceback

                traceback.print_exc()
                raise Exception(f"Failed to update ULID in TOML file {real_path}: {e!s}") from e
        new_plan = Plan(**toml_doc)  # type: ignore  — ValidationError propagates to caller

        return new_plan

    def __repr__(self):
        return f"<Plan>(ulid='{self.ulid}')"

    def add_event(self, event: EventModel, toml_path: str | os.PathLike | None = None):
        """Append ``event`` to the plan's file: ``toml_path``, else the file it was loaded from.

        A read-modify-write of the whole file: a caller with more than one writer (control's
        execution, one thread per unit) holds its own lock around it (5.2).
        """
        file = toml_path if toml_path is not None else (self.model_extra or {}).get("toml_file")
        if file is None:
            return

        with open(file) as f:
            toml_doc = tomlkit.load(f)

        if "events" not in toml_doc:
            toml_doc["events"] = tomlkit.aot()

        new_event = tomlkit.table()
        new_event.add("what", event.what)
        new_event.add("details", event.details)
        new_event.add("when", event.when)
        toml_doc["events"].append(new_event)  # type: ignore

        with open(file, "w") as f:
            f.write(tomlkit.dumps(toml_doc))


if __name__ == "__main__":
    import json
    import sys
    import traceback

    if len(sys.argv) < 2:
        print("Usage: python common/models/plans.py <plan-file.toml>")
        sys.exit(1)

    toml_path = sys.argv[1]
    try:
        plan = Plan.from_toml_file(toml_path)
        data = plan.model_dump()
        print(json.dumps(data, indent=2, default=str))
    except Exception:  # noqa: BLE001 -- top-level entry point: print the traceback and exit non-zero
        traceback.print_exc()
        sys.exit(2)
