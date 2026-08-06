import math

import astropy.coordinates
from pydantic import BaseModel, Field, field_validator

from common.models.constraints import RepeatsModel
from common.models.science import ScienceModel

# What a coordinate may look like, for error messages. astropy accepts all of it:
# ':' or whitespace as separator, one or two digits per component, any number of
# decimals on the seconds, and surrounding whitespace trimmed.
_ACCEPTED_FORMS = "sexagesimal ('5:34:32.5', '05 34 32.5') or decimal ('5.575')"


def _parse_angle(value: str | float, *, unit: str, low: float, high: float, high_included: bool, kind: str) -> float:
    """
    Parse a sexagesimal or decimal angle and range-check it. No normalisation.

    Angle is used rather than Longitude/Latitude because both of those decide the
    range question themselves and so hid the check below. Longitude WRAPS: it turned
    an RA of 25 into 1.0 and -1 into 23.0, silently accepting a typo as a different
    target, and left `0 <= ra < 24` unable to fail. Latitude raises before the check
    is reached, so the message an operator saw came from astropy, not from here.
    Angle does neither -- it parses and stops -- which makes the bounds below the
    only thing that decides what is acceptable, for decimal and sexagesimal alike.
    """
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise ValueError(f"{kind} is empty; expected {_ACCEPTED_FORMS}")
    try:
        angle = float(astropy.coordinates.Angle(value, unit=unit).value)
    except ValueError as e:
        # Every astropy angle error (IllegalHourError and friends) subclasses
        # ValueError. Its own wording is about parser columns; say what we wanted.
        raise ValueError(f"{kind}: cannot parse {value!r} -- expected {_ACCEPTED_FORMS} ({e})") from e
    if not math.isfinite(angle):
        # inf parses to nan rather than raising, and every comparison against nan is
        # False, so an unguarded range test would report it as merely out of range.
        raise ValueError(f"{kind}: {value!r} is not a finite number")
    if not (low <= angle <= high if high_included else low <= angle < high):
        raise ValueError(f"{kind} {angle} is out of range [{low}, {high}{']' if high_included else ')'}")
    return angle


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
                "tooltip": "Sexagesimal:<ul><li>&nbsp;<b>05:34:32.5</b><li>&nbsp;<b>05 34 32.5</b></li></ul>&nbsp;or decimal hours <ul><li>&nbsp;<b>5.575</b></li></ul>",
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
                "tooltip": "Sexagesimal:<ul><li>&nbsp;<b>+22:00:52.5</b></li><li>&nbsp;<b>-22 00 52.5</b></li></ul>&nbsp;or decimal degrees <ul><li>&nbsp;<b>22.014</b></li></ul>",
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
    requested_exposure_duration: float | None = Field(
        default=None,
        gt=0,
        le=3600,
        json_schema_extra={
            "ui": {
                "error_message": "Positive decimal between 0 and 3600",
                "label": "Duration per exposure",
                "widget": "number",
                "unit": "seconds",
                "summary": True,
                "required": True,
                "section": {
                    "label": "Exposure Series",
                    "tooltip": "Series of exposures to be scheduled for this target",
                },
            },
        },
    )
    max_exposure_duration: float | None = Field(
        default=None,
        gt=0,
        le=3600,
        json_schema_extra={
            "ui": {
                "error_message": "Positive decimal between 0 and 3600",
                "label": "Max duration per exposure",
                "widget": "number",
                "unit": "seconds",
                "tooltip": "Plans with longer durations will not be batched together,<br>&nbsp;to prevent over exposure",
                "section": "Exposure Series",
            }
        },
    )
    requested_number_of_exposures: int | None = Field(
        default=1,
        gt=0,
        json_schema_extra={
            "ui": {
                "error_message": "Positive integer",
                "label": "Number of exposures",
                "widget": "number",
                "required": True,
                "section": {
                    "label": "Exposure Series",
                },
            }
        },
    )
    repeats: RepeatsModel = Field(
        default_factory=RepeatsModel,
        json_schema_extra={
            "ui": {
                "label": "repeats",
                "tooltip": "When and how much should the exposure series be rescheduled?",
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
        return _parse_angle(value, unit="hour", low=0.0, high=24.0, high_included=False, kind="RA")

    @field_validator("dec_degrees")
    @classmethod
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float in [-90, 90]
        """
        return _parse_angle(value, unit="deg", low=-90.0, high=90.0, high_included=True, kind="Dec")

    def __repr__(self) -> str:
        return f"Target(ra_hours={self.ra_hours}, dec_degrees={self.dec_degrees})"
