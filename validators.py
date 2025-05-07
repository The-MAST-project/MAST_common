import re
from typing import Annotated

from fastapi import HTTPException, Query

# Regex for valid hour angle in sexagesimal format with optional fractional seconds
RA_SEXAGESIMAL_REGEX = (
    r"^(2[0-3]|1[0-9]|0?[0-9]):([0-5]?[0-9]):([0-5]?[0-9])(\.\d{1,3})?$"
)

# Dependency to validate Right Ascension


def validate_ra(ra: str):
    # Check if RA is a valid float
    try:
        ra_float = float(ra)
        if 0 <= ra_float < 24:
            return ra_float
    except ValueError:
        pass

    # Check if RA matches the sexagesimal regex
    if re.match(RA_SEXAGESIMAL_REGEX, ra):
        return ra

    # If neither float nor regex matches, raise an error
    raise HTTPException(
        status_code=400,
        detail="Invalid Right Ascension format. Must be a float (0 <= RA < 24) or 'hh:mm:ss[.fff]'.",
    )


# Regex for valid declination in sexagesimal format with optional fractional seconds
DEC_SEXAGESIMAL_REGEX = r"^[+-]?(90:00:00(\.0{1,3})?|([0-8]?[0-9]):([0-5]?[0-9]):([0-5]?[0-9])(\.\d{1,3})?)$"


def validate_dec(dec: str):
    # Check if DEC is a valid float
    try:
        dec_float = float(dec)
        if -90 <= dec_float <= 90:
            return dec_float
    except ValueError:
        pass

    # Check if DEC matches the sexagesimal regex
    if re.match(DEC_SEXAGESIMAL_REGEX, dec):
        return dec

    # If neither float nor regex matches, raise an error
    raise HTTPException(
        status_code=400,
        detail="Invalid Declination format. Must be a float (-90 <= DEC <= 90) or '+dd:mm:ss[.fff]'.",
    )
