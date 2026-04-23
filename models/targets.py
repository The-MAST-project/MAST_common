import astropy.coordinates
from pydantic import BaseModel, Field, field_validator
from .constraints import RepeatsModel
from .science import ScienceModel


class Target(BaseModel):
    name : str | None = Field(
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
        ra = float(astropy.coordinates.Longitude(value, unit="hour").value)
        if not (0 <= ra < 24):
            raise ValueError(f"RA decimal value {ra} is out of range [0, 24)")
        return ra

    @field_validator("dec_degrees")
    @classmethod
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float in [-90, 90]
        """
        dec = float(astropy.coordinates.Latitude(value, unit="deg").value)
        if not (-90 <= dec <= 90):
            raise ValueError(f"Dec decimal value {dec} is out of range [-90, 90]")
        return dec

    def __repr__(self) -> str:
        return f"Target(ra_hours={self.ra_hours}, dec_degrees={self.dec_degrees})"
