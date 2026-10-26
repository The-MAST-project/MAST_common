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


#: The one requested_units entry that is not a specifier: the plan runs on whatever units
#: are deployed and not in maintenance (plans/control_acquisition_design.md 3.1).
ANY_UNITS = "any"

_UNIT_SPECIFIER = re.compile(r"^(?:(?P<site>[A-Za-z]\w*):)?(?:(?P<building>[A-Za-z]\w*):)?(?P<ids>[\w,-]+)$")


def is_unit_specifier(specifier: str) -> bool:
    """Syntax only -- `w`, `mast01`, `ns:1,3,5`, `ns:10-17`, `ns:north:9` -- with no
    configuration, so a plan can be validated wherever it is loaded."""
    return bool(_UNIT_SPECIFIER.match(specifier)) and specifier != ANY_UNITS


def expand_units(specifiers: list[str], sites: list, local_site) -> list[str]:
    """Unit hostnames (``mast01``) for unit specifiers, in order, duplicates dropped.

    ``<site>:`` defaults to ``local_site``; ``<site>:<building>:<n>`` is the building's
    ``n``-th unit; ids are site-relative (``1``, ``01``, ``mast01``), with ``,`` lists and
    ``a-b`` ranges. Raises ValueError for anything that names no unit of a known site.

    Does **not** filter on deployment or maintenance, unlike `parse_units`, which drops
    those with a log line: whoever runs the plan says why a unit is not used
    (plans/control_acquisition_design.md 5.6).
    """
    names: list[str] = []
    for specifier in specifiers:
        match = _UNIT_SPECIFIER.match(specifier)
        if not match or specifier == ANY_UNITS:
            raise ValueError(f"not a unit specifier: {specifier!r}")
        site_name = match.group("site")
        site = local_site if site_name is None else next((s for s in sites if s.name == site_name), None)
        if site is None:
            raise ValueError(f"{specifier!r}: no site named {site_name!r} (sites: {[s.name for s in sites]})")
        ids = parse_unit_ids(match.group("ids"))
        building_name = match.group("building")
        if building_name is not None:
            building = next((b for b in site.buildings if building_name in b.names), None)
            if building is None:
                raise ValueError(f"{specifier!r}: no building {building_name!r} at '{site.name}'")
            in_building = building.units or []
            hostnames = []
            for i in ids:
                if not i.isdigit() or not 1 <= int(i) <= len(in_building):
                    raise ValueError(f"{specifier!r}: building {building_name!r} has units 1-{len(in_building)}")
                hostnames.append(in_building[int(i) - 1])
        else:
            hostnames = site.normalize_unit_specifier(ids)
        known = site.unit_ids if isinstance(site.unit_ids, list) else site.normalize_unit_specifier(site.unit_ids)
        for hostname in hostnames:
            if hostname not in known:
                raise ValueError(f"{specifier!r}: '{hostname}' is not a unit of site '{site.name}'")
            if hostname not in names:
                names.append(hostname)
    return names


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


def parse_target(
    ra_j2000_hours: str | float | None, dec_j2000_degs: str | float | None
) -> tuple[float | None, float | None]:
    """Target coordinates as decimal hours and degrees, in whatever form they arrive.

    One call, whatever the form. `float()` is NOT enough and never was: `RA_PATTERN`
    deliberately accepts space-separated sexagesimal, so `"03 08 10.142"` passes the
    query validation and then fails conversion -- the same defect MAST_unit's `acquirer.py`
    records having already fixed once, in the comment above its own parsing block. The
    parsers take sexagesimal, decimal and surrounding whitespace alike.

    None is passed through rather than defaulted: it means "take it from the mount", and
    only the unit's acquirer can do that. Note the emptiness test is explicit rather than
    truthiness, so an RA of exactly 0 hours is a coordinate and not a missing value.

    Moved here from MAST_unit's `flux_metering/session.py` so MAST_control converts the
    coordinates it validates with `RA_PATTERN` / `DEC_PATTERN` the same way.
    """
    ra = None if ra_j2000_hours is None or ra_j2000_hours == "" else sexagesimal_hours_to_decimal(ra_j2000_hours)
    dec = None if dec_j2000_degs is None or dec_j2000_degs == "" else sexagesimal_degrees_to_decimal(dec_j2000_degs)
    return ra, dec


def _usable_pair(ra_j2000_hours, dec_j2000_degs) -> tuple[float, float] | None:
    """Both coordinates, both converting; else None."""
    try:
        ra, dec = parse_target(ra_j2000_hours, dec_j2000_degs)
    except ValueError:
        return None
    return (ra, dec) if ra is not None and dec is not None else None


def target_coordinates(
    ra_j2000_hours: str | float | None,
    dec_j2000_degs: str | float | None,
    object_name: str | None = None,
    resolve_timeout: float | None = None,
):
    """Where a target is, by the one precedence rule (plans/control_acquisition_design.md 3.8):

    1. **Both coordinates, both valid**: used as given, and the name is not resolved at
       all -- it is a label. Returns ``(ra, dec, None)``.
    2. **Otherwise the name, if there is one**, by `resolve_object_name`. "Otherwise" covers
       missing AND unusable: a coordinate that will not convert is not a coordinate.
       Returns ``(ra, dec, resolved)``; a name that does not resolve raises `ObjectNameError`
       and never falls through.
    3. **Otherwise**: nothing at all was given -> ``(None, None, None)``, for the caller to
       decide (the unit takes the mount's position; a plan is invalid). Coordinates given
       but unusable, or only one of them -> `ValueError`, saying so.

    Beside `parse_target`, not in place of it: that one converts whatever it is given and
    its callers unpack two values.
    """
    from common.object_resolver import TOTAL_TIMEOUT_SECONDS, resolve_object_name

    pair = _usable_pair(ra_j2000_hours, dec_j2000_degs)
    if pair is not None:
        return pair[0], pair[1], None

    if object_name:
        timeout = TOTAL_TIMEOUT_SECONDS if resolve_timeout is None else resolve_timeout
        resolved = resolve_object_name(object_name, total_timeout=timeout)
        return resolved.ra_j2000_hours, resolved.dec_j2000_degs, resolved

    given = [v for v in (ra_j2000_hours, dec_j2000_degs) if v is not None and v != ""]
    if not given:
        return None, None, None
    raise ValueError(
        f"coordinates were supplied but unusable (ra={ra_j2000_hours!r}, dec={dec_j2000_degs!r}); "
        "supply both as valid J2000, or an object name instead"
    )


if __name__ == "__main__":
    # units = parse_units(['w', 'ns:10-12', 'ns:1,3,5', 'ns:south:3-5'])
    # print(units)
    print(sexagesimal_degrees_to_decimal("2:53:32.8 "))
