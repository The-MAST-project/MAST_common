from pydantic import BaseModel, Field, field_validator

from common.models.events import EventModel

# One implementation, beside the FastAPI patterns that guard the same grammar, so a
# coordinate cannot mean one thing to a plan and another to an endpoint.
from common.models.science import ScienceModel
from common.parsers import sexagesimal_degrees_to_decimal, sexagesimal_hours_to_decimal, target_coordinates


class Target(BaseModel):
    name: str | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Name",
                "widget": "text",
            },
            "searchable": "text",
        },
    )
    magnitude: float | None = Field(
        default=None,
        json_schema_extra={
            "ui": {
                "label": "Magnitude",
                "widget": "number",
            },
            "searchable": "range",
        },
    )
    ra_hours: str | float = Field(
        description="RightAscension [sexagesimal or decimal]",
        json_schema_extra={
            "ui": {
                "label": "RA",
                "pattern": r"^([01]?\d|2[0-3])[: ][0-5]\d[: ][0-5]\d(?:\.\d+)?$|^([01]?\d|2[0-3])(?:\.\d+)?$",
                "error_message": "Sexagesimal (colon or space separated) or decimal hours [0:24]",
                "widget": "text",
                "unit": "hours",
                "summary": True,
                "required": True,
                "tooltip": "Sexagesimal:<ul><li>&nbsp;<b>05:34:32.5</b><li>&nbsp;<b>05 34 32.5</b></li></ul>"
                "&nbsp;or decimal hours <ul><li>&nbsp;<b>5.575</b></li></ul>",
            },
            "searchable": "range",
        },
    )
    dec_degrees: str | float = Field(
        description="Declination [sexagesimal or decimal]",
        json_schema_extra={
            "ui": {
                "label": "Dec",
                "pattern": r"^[+-]?([0-8]?\d|90)[: ][0-5]\d[: ][0-5]\d(?:\.\d+)?$|^[+-]?([0-8]?\d|90)(?:\.\d+)?$",
                "widget": "text",
                "unit": "degrees",
                "summary": True,
                "required": True,
                "tooltip": "Sexagesimal:<ul><li>&nbsp;<b>+22:00:52.5</b></li><li>&nbsp;<b>-22 00 52.5</b></li></ul>"
                "&nbsp;or decimal degrees <ul><li>&nbsp;<b>22.014</b></li></ul>",
                "error_message": "Sexagesimal (colon or space separated) or decimal degrees [-90:90]",
            },
            "searchable": "range",
        },
    )
    science: ScienceModel = Field(
        default_factory=ScienceModel,
        json_schema_extra={
            "ui": {
                "label": "Science",
                "tooltip": "Science case and classification for this target",
            }
        },
    )

    @field_validator("ra_hours")
    @classmethod
    def validate_ra(cls, value):
        """
        Validates RightAscension inputs
        :param value: sexagesimal string or float
        :return: a float in [0, 24)
        """
        return sexagesimal_hours_to_decimal(value)

    @field_validator("dec_degrees")
    @classmethod
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float in [-90, 90]
        """
        return sexagesimal_degrees_to_decimal(value)

    def __repr__(self) -> str:
        return f"Target(ra_hours={self.ra_hours}, dec_degrees={self.dec_degrees})"


def resolve_target(target: dict, resolve_timeout: float | None = None) -> tuple[dict, EventModel | None]:
    """``target`` with decimal coordinates written in, and the `resolved` event if a name had
    to be resolved for them (plans/control_acquisition_design.md 3.8).

    Run on the raw dict whenever a plan is **written** -- never by `Target`'s validator,
    which runs on every load -- so the file carries coordinates and later loads never touch
    the network. Usable coordinates win and the name stays a label; otherwise the name is
    resolved. Raises `ObjectNameError` for a name that does not resolve, ValueError when
    there is neither.
    """
    ra, dec, name = target.get("ra_hours"), target.get("dec_degrees"), target.get("name")
    ra_hours, dec_degrees, resolved = target_coordinates(ra, dec, name, resolve_timeout)
    if ra_hours is None or dec_degrees is None:
        raise ValueError("a target needs both coordinates, or a name to resolve")
    out = {**target, "ra_hours": ra_hours, "dec_degrees": dec_degrees}
    if resolved is None:
        return out, None
    details = []
    if ra not in (None, "") or dec not in (None, ""):
        details.append(f"coordinates unusable (ra={ra!r}, dec={dec!r}), resolved the name instead")
    details.append(f"{name} -> ra={ra_hours:.6f}h dec={dec_degrees:+.6f}d via {resolved.database or resolved.resolver}")
    if resolved.canonical_name:
        details.append(f"canonical name: {resolved.canonical_name}")
    return out, EventModel(what="resolved", details=details)
