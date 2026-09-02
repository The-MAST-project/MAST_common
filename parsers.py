import math
import re

import astropy.coordinates

from common.config import Config
from common.mast_logging import get_logger
from common.utils import function_name

logger = get_logger(__name__)


def parse_units(specifiers: list[str] | str) -> list[str]:
    """
    The ultimate unit-specifier parser

    Valid specifiers:
     - 'w'          - unit named 'w' in the (default) local site
     - 'wis:w'      - unit named 'w' in site named 'wis'
     - 'ns:north:9' - unit '9' in building 'north' of site 'ns'
     - 'ns:10-17'   - units '10' to '17' at site 'ns'

    :param specifiers: one or more unit specifiers
    :return: list of site:unit-id pairs
    """
    op = function_name()
    ret: list[str] = []
    if isinstance(specifiers, str):
        specifiers = [specifiers]

    sites = Config().sites
    local_site = Config().local_site
    assert local_site is not None, "cannot determine local site from configuration"
    units_spec = None
    building = None
    site = None

    for specifier in specifiers:
        for spec in specifier.split():
            site = None
            units_spec = None
            building = None
            building_name: str | None = None

            match = re.match(
                r"^(?:(?P<site>\w+):)?(?:(?P<building>\w+):)?(?P<units>[,a-zA-Z0-9_-]+)$",
                spec,
            )
            if match:
                # <site>:<building>:<units>
                site_name = match.group(1)
                building_name = match.group(2)
                units_spec = match.group(3)
            else:
                match = re.match(r"^(?:(?P<site>\w+):{1,2})?(?P<units>\w+)$", spec)
                if match:
                    # <site>:<units>
                    site_name = match.group("site")
                    units_spec = match.group("units")
                else:
                    logger.error(f"{op}: Invalid units spec: {specifier}")
                    continue

            if site_name:
                site = next((s for s in sites if s.name == site_name), None)
                if site is None:
                    logger.error(f"{op}: Invalid site: '{site_name}', defined sites: {[s.name for s in sites]}")
                    continue
            else:
                site = local_site
                site_name = site.name

            if building_name:
                for b in site.buildings:
                    if building_name in b.names:
                        building = b
                        break

                if not building:
                    # a building was specified but it's not valid
                    logger.error(f"{op}: Invalid building: '{building_name}' at site '{site.name}'")
                    continue

            for unit in parse_unit_ids(units_spec):
                if building:
                    if unit not in building.units:
                        logger.error(f"{op}: {unit=} not in {building.units=}")
                        continue
                    units_numbering_base = sum([len(b.units) for b in site.buildings[0 : site.buildings.index(building)]])
                    unit_id = str(int(unit) + units_numbering_base)
                    if unit_id not in site.valid_ids:
                        logger.error(f"{op}: {unit_id=} not valid at '{site.name}' ({site.valid_ids=}), skipped.")
                    elif unit_id not in site.deployed_units:
                        logger.error(f"{op}: {unit_id=} not deployed at '{site.name}' ({site.deployed_units=}), skipped.")
                    elif unit_id in site.units_in_maintenance:
                        logger.error(
                            f"{op}: {unit_id=} in maintenance at '{site.name}' ({site.units_in_maintenance=}), skipped."
                        )
                    else:
                        ret.append(f"{site_name}:{unit_id}")

                elif unit in site.valid_ids:
                    unit_id = unit
                    if unit_id not in site.deployed_units:
                        logger.error(f"{op}: {unit_id=} not deployed at '{site.name}' ({site.deployed_units=}), skipped.")
                    elif unit_id in site.units_in_maintenance:
                        logger.error(
                            f"{op}: {unit_id=} in maintenance at '{site.name}' ({site.units_in_maintenance=}), skipped."
                        )
                    else:
                        ret.append(f"{site_name}:{unit_id}")
                else:
                    logger.error(f"{op}: Invalid unit: '{unit}' at '{site.name=}', known units: {site.valid_ids}")

    return ret


def parse_unit_ids(units_spec: str) -> list[str]:
    """
    Parses and validates a units specifier (a string):

    :param units_spec: a units specifier, e.g. "mastw" or "w" or "1-5" or "3,4,2-6"
    :return: list of fully qualified unit names
    """
    ret = []

    for spec in units_spec.split(","):
        if "-" in spec:
            word = spec.split("-")
            if word[0].isdigit() and word[1].isdigit():
                for i in range(int(word[0]), int(word[1]) + 1):
                    ret.append(str(i))
        else:
            ret.append(spec)

    return ret


# Coordinate patterns for FastAPI `Query(pattern=...)` guards, kept here -- beside
# the parsers they guard -- because the two must agree. They did not: MAST_unit
# carried two divergent copies (one allowing ':' or space, one only ':'), each
# stricter than these parsers, so every coordinate error in the unit logs of
# 2026-08-04 was the guard rejecting input the parser was written to accept
# (MAST_unit#88).
#
# Deliberately permissive, matching what astropy accepts and Target validates:
# ':' or whitespace between components, one or two digits per component, any
# number of decimals on the seconds, and surrounding whitespace (the parsers
# strip; the previous '$'-anchored patterns rejected a trailing space outright).
#
# Range is NOT expressed here. A regex that also bounded 0-23h/0-59m would be
# unreadable and would duplicate a check that belongs to one layer -- parse_angle
# owns it.
_SEXAGESIMAL = r"\d{1,2}[: ]\d{1,2}[: ]\d{1,2}(?:\.\d+)?"
_DECIMAL = r"\d{1,2}(?:\.\d+)?"

RA_PATTERN = rf"^\s*(?:{_SEXAGESIMAL}|{_DECIMAL})\s*$"
DEC_PATTERN = rf"^\s*[+-]?(?:{_SEXAGESIMAL}|{_DECIMAL})\s*$"

_ACCEPTED_FORMS = "sexagesimal ('5:34:32.5', '05 34 32.5') or decimal ('5.575')"


def parse_angle(value: str | float, *, unit: str, low: float, high: float, high_included: bool, kind: str) -> float:
    """
    Parse a sexagesimal or decimal angle and range-check it. No normalisation.

    Angle is used rather than Longitude/Latitude because both of those answer the
    range question themselves, and so hid the check. Longitude WRAPS: it turned an
    RA of 25 into 1.0 and -1 into 23.0, so the unit endpoints accepted a mistyped
    coordinate and slewed hours away from the target, with nothing in the log.
    Latitude raises before any check of ours is reached, so its message reached the
    operator instead of one naming the field. Angle does neither -- it parses and
    stops -- which makes the bounds passed in here the only thing deciding what is
    acceptable, for decimal and sexagesimal alike.

    Raises ValueError, having logged it: a coordinate this service refused is worth
    a line in the log whether or not the caller reports it (MAST_unit#88).
    """
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise _rejected(kind, f"{kind} is empty; expected {_ACCEPTED_FORMS}")
    try:
        angle = float(astropy.coordinates.Angle(value, unit=unit).value)
    except ValueError as e:
        # Every astropy angle error (IllegalHourError and friends) subclasses
        # ValueError. Its own wording is about parser columns; say what we wanted.
        raise _rejected(kind, f"{kind}: cannot parse {value!r} -- expected {_ACCEPTED_FORMS} ({e})") from e
    if not math.isfinite(angle):
        # inf parses to nan rather than raising, and every comparison against nan is
        # False, so an unguarded range test would report it as merely out of range.
        raise _rejected(kind, f"{kind}: {value!r} is not a finite number")
    if not (low <= angle <= high if high_included else low <= angle < high):
        raise _rejected(kind, f"{kind} {angle} is out of range [{low}, {high}{']' if high_included else ')'}")
    return angle


def _rejected(kind: str, message: str) -> ValueError:
    logger.error(f"rejected {kind}: {message}")
    return ValueError(message)


def sexagesimal_hours_to_decimal(value: str | float) -> float:
    """Right Ascension, in decimal hours [0, 24)."""
    return parse_angle(value, unit="hour", low=0.0, high=24.0, high_included=False, kind="RA")


def sexagesimal_degrees_to_decimal(value: str | float) -> float:
    """Declination, in decimal degrees [-90, 90]."""
    return parse_angle(value, unit="deg", low=-90.0, high=90.0, high_included=True, kind="Dec")


if __name__ == "__main__":
    # units = parse_units(['w', 'ns:10-12', 'ns:1,3,5', 'ns:south:3-5'])
    # print(units)
    print(sexagesimal_degrees_to_decimal("2:53:32.8 "))
