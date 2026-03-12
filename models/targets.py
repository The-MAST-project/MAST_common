import astropy.coordinates
from pydantic import BaseModel, Field, field_validator


class Target(BaseModel):
    ra_hours: str | float = Field(description="RightAscension [sexagesimal or decimal]")
    dec_degrees: str | float = Field(description="Declination [sexagesimal or decimal]")

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
