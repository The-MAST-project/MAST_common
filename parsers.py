import math
import re

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


# Coordinate grammar, one definition for every layer that reads a coordinate: the
# FastAPI `Query(pattern=...)` guards, `Target.ui.pattern` (which the GUI's Alpine
# validation compiles as a JS RegExp), and the parsers below. The guards and the
# parsers must agree; they did not, and every coordinate error in the unit logs of
# 2026-08-04 was the guard rejecting input the parser accepted (MAST_unit#88).
#
# Keep these in the subset Python `re` and JS `RegExp` share: no inline flags, no
# named groups, no lookbehind.
#
# Leading and trailing whitespace is ignored. Between components there is exactly one
# ':' or one space, and the same one both times (no '5:34 32'). The backreference
# `\1` relies on the separator being the pattern's only capture group, which holds
# in both RA_PATTERN and DEC_PATTERN. One or two digits per component; any number of
# decimals on the seconds.
#
# The lettered form is `5h34m32.5s` for RA and `22d00m52.5s` for Dec: all three letters
# present, no whitespace inside, and all lower case or all upper case (`5H34M32.5S`),
# never mixed (`5H34m32s`). Case is spelled as two alternatives because the patterns
# may not carry an inline `(?i)` flag. The letter pairs with the field: RA takes h, Dec
# takes d, so `22d00m52s` is not an RA.
#
# Range is NOT in the patterns. A regex that also bounded 0-23h / 0-59m would be
# unreadable and would duplicate a check that belongs to one layer: `parse_angle`
# owns it, and does it itself -- astropy rolls minute/second 60 over into the next
# unit (with a warning) and never range-checks the total, so it is not used.
_SEXAGESIMAL = r"\d{1,2}([: ])\d{1,2}\1\d{1,2}(?:\.\d+)?"
_DECIMAL = r"\d{1,2}(?:\.\d+)?"


def _lettered(degrees_or_hours: str) -> str:
    """The pattern for `5h34m32.5s` / `22d00m52.5s`, in one case or the other."""
    lower = degrees_or_hours
    upper = degrees_or_hours.upper()
    return (
        rf"\d{{1,2}}{lower}\d{{1,2}}m\d{{1,2}}(?:\.\d+)?s"
        rf"|\d{{1,2}}{upper}\d{{1,2}}M\d{{1,2}}(?:\.\d+)?S"
    )


RA_PATTERN = rf"^\s*(?:{_SEXAGESIMAL}|{_lettered('h')}|{_DECIMAL})\s*$"
DEC_PATTERN = rf"^\s*[+-]?(?:{_SEXAGESIMAL}|{_lettered('d')}|{_DECIMAL})\s*$"

_ACCEPTED_FORMS = {
    "h": "sexagesimal ('5:34:32.5', '05 34 32.5'), lettered ('5h34m32.5s') or decimal ('5.575')",
    "d": "sexagesimal ('+22:00:52.5', '-22 00 52.5'), lettered ('+22d00m52.5s') or decimal ('22.014')",
}

# The parser's own reading of a string. The sign is accepted for RA too, so that '-1'
# is reported as out of range, not as unparseable; RA_PATTERN is what refuses it up front.
_SEXAGESIMAL_RE = re.compile(r"^([+-]?)(\d{1,2})([: ])(\d{1,2})\3(\d{1,2}(?:\.\d+)?)$")
_DECIMAL_RE = re.compile(r"^([+-]?)(\d{1,2}(?:\.\d+)?)$")


def _lettered_res(unit: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    """`5h34m32.5s` and `5H34M32.5S` -- each letter case separately, so cases never mix."""

    def build(u: str, m: str, s: str) -> re.Pattern[str]:
        return re.compile(rf"^([+-]?)(\d{{1,2}}){u}(\d{{1,2}}){m}(\d{{1,2}}(?:\.\d+)?){s}$")

    return build(unit, "m", "s"), build(unit.upper(), "M", "S")


_LETTERED_RES = {"h": _lettered_res("h"), "d": _lettered_res("d")}


def parse_angle(value: str | float, *, unit: str, low: float, high: float, high_included: bool, kind: str) -> float:
    """
    Parse a sexagesimal, lettered or decimal angle and range-check it. No normalisation.

    `unit` is 'h' (hours: `5h34m32s`) or 'd' (degrees: `22d00m52s`); it selects the
    letter the lettered form must use.

    Every check is spelled out here, none is left to a library:

    - minutes and seconds must be in [0, 60). '5:60:00' is a typo, not 6 hours, and is
      refused rather than rolled over;
    - the total must be in the bounds passed in. Longitude WRAPS (it turned an RA of 25
      into 1.0 and -1 into 23.0, so the unit endpoints slewed hours away from the
      target with nothing in the log) and Latitude raises before any check of ours runs,
      so neither is used. The bounds passed in are the only thing deciding what is
      acceptable, for decimal and sexagesimal alike;
    - the result is finite.

    Raises ValueError, having logged it: a coordinate this service refused is worth
    a line in the log whether or not the caller reports it (MAST_unit#88).
    """
    if isinstance(value, str):
        value = value.strip()
        if not value:
            raise _rejected(kind, f"{kind} is empty; expected {_ACCEPTED_FORMS[unit]}")
        sexagesimal = _SEXAGESIMAL_RE.match(value)
        decimal = _DECIMAL_RE.match(value)
        lettered = next((m for rx in _LETTERED_RES[unit] if (m := rx.match(value))), None)
        if clock := sexagesimal or lettered:
            sign, whole, *_separator, minutes, seconds = clock.groups()
            for name, component in (("minutes", float(minutes)), ("seconds", float(seconds))):
                if not 0 <= component < 60:
                    raise _rejected(kind, f"{kind}: {name} {component:g} in {value!r} is out of range [0, 60)")
            angle = int(whole) + int(minutes) / 60 + float(seconds) / 3600
        elif decimal:
            sign, number = decimal.groups()
            angle = float(number)
        else:
            raise _rejected(kind, f"{kind}: cannot parse {value!r} -- expected {_ACCEPTED_FORMS[unit]}")
        if sign == "-":
            angle = -angle
    else:
        try:
            angle = float(value)
        except (TypeError, ValueError) as e:
            raise _rejected(kind, f"{kind}: cannot parse {value!r} -- expected {_ACCEPTED_FORMS[unit]}") from e
    if not math.isfinite(angle):
        raise _rejected(kind, f"{kind}: {value!r} is not a finite number")
    if not (low <= angle <= high if high_included else low <= angle < high):
        raise _rejected(kind, f"{kind} {angle} is out of range [{low}, {high}{']' if high_included else ')'}")
    return angle


def _rejected(kind: str, message: str) -> ValueError:
    logger.error(f"rejected {kind}: {message}")
    return ValueError(message)


def sexagesimal_hours_to_decimal(value: str | float) -> float:
    """Right Ascension, in decimal hours [0, 24)."""
    return parse_angle(value, unit="h", low=0.0, high=24.0, high_included=False, kind="RA")


def sexagesimal_degrees_to_decimal(value: str | float) -> float:
    """Declination, in decimal degrees [-90, 90]."""
    return parse_angle(value, unit="d", low=-90.0, high=90.0, high_included=True, kind="Dec")


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
