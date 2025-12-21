import astropy.coordinates
from pydantic import BaseModel, Field, field_validator


class Target(BaseModel):
    ra: str | float = Field(description="RightAscension [sexagesimal or decimal]")
    dec: str | float = Field(description="Declination [sexagesimal or decimal]")

    @field_validator("ra")
    @classmethod
    def validate_ra(cls, value):
        """
        Validates RightAscension inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        ra = astropy.coordinates.Longitude(value, unit="hour").value
        return float(ra)  # converts np.float64 to float

    @field_validator("dec")
    @classmethod
    def validate_dec(cls, value):
        """
        Validates Declination inputs
        :param value: sexagesimal string or float
        :return: a float
        """
        dec = astropy.coordinates.Latitude(value, unit="deg").value
        return float(dec)  # converts np.float64 to float
