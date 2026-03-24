import astropy.coordinates
from pydantic import BaseModel, Field, field_validator
from common.models.constraints import RepeatsModel


class Target(BaseModel):
    ra_hours: str | float = Field(
        description="RightAscension [sexagesimal or decimal]",
        ge=0,
        le=24,
        json_schema_extra={
            "ui": {
                "label": "RA",
                # RA: sexagesimal (no sign, 0–23h) OR decimal hours
                "pattern": r"^\d{1,2}[: ][0-5]?\d[: ][0-5]?\d(?:\.\d+)?$|^\d+(?:\.\d+)?$",
                "error_message": "Sexagesimal (colon or space separated) or decimal hours [0:24]",
                "widget": "text",
                "unit": "hours",
                "summary": True,
                "required": True,
                "tooltip": "Sexagesimal:<ul><li>&nbsp;<b>05:34:32.5</b><li>&nbsp;<b>05 34 32.5</b></li></ul>&nbsp;or decimal hours <ul><li>&nbsp;<b>5.575</b></li></ul>",
            }
        },
    )
    dec_degrees: str | float = Field(
        description="Declination [sexagesimal or decimal]",
        ge=-90,
        le=90,
        json_schema_extra={
            "ui": {
                "label": "Dec",
                # Dec: sexagesimal (optional sign) OR decimal degrees (optional sign)
                "pattern": r"^[+-]?\d{1,2}[: ][0-5]?\d[: ][0-5]?\d(?:\.\d+)?$|^[+-]?\d+(?:\.\d+)?$",
                "widget": "text",
                "unit": "degrees",
                "summary": True,
                "required": True,
                "tooltip": "Sexagesimal:<ul><li>&nbsp;<b>+22:00:52.5</b></li><li>&nbsp;<b>-22 00 52.5</b></li></ul>&nbsp;or decimal degrees <ul><li>&nbsp;<b>22.014</b></li></ul>",
                "error_message": "Sexagesimal (colon or space separated) or decimal degrees [-90:90]",
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
            }
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
                "section": {"label": "Exposure Series"},
            }
        },
    )
    repeats: RepeatsModel = Field(
        default_factory=RepeatsModel,
        json_schema_extra={"ui": {
            "label": "repeats",
            "tooltip": "When and how much should the exposure series be rescheduled?",
        }},
    )

    @field_validator("ra_hours")
    @classmethod
    def validate_ra(cls, value):
        """
        Validates RightAscension inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        ra = astropy.coordinates.Longitude(value, unit="hour").value
        return float(ra)  # converts np.float64 to float

    @field_validator("dec_degrees")
    @classmethod
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        dec = astropy.coordinates.Latitude(value, unit="deg").value
        return float(dec)  # converts np.float64 to float

    def __repr__(self) -> str:
        return f"Target(ra_hours={self.ra_hours}, dec_degrees={self.dec_degrees})"
