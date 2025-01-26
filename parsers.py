import logging
from typing import List

import astropy.coordinates
import astropy.units as u

from common.config import Config, Site
from common.utils import function_name
from common.mast_logging import init_log
import re

logger = logging.Logger('parsers')
init_log(logger)


def parse_units(specifiers: List[str] | str) -> List[str]:
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
    errors: List[str] = []
    ret: List[str] = []
    if isinstance(specifiers, str):
        specifiers = [specifiers]

    sites = Config().sites
    local_site: Site = [s for s in sites if s.local][0]
    units_spec = None
    building = None
    site = None

    for specifier in specifiers:
        for spec in specifier.split():
            site = None
            units_spec = None
            building = None
            building_name: str | None = None

            match = re.match(r'^(?:(?P<site>\w+):)?(?:(?P<building>\w+):)?(?P<units>[,a-zA-Z0-9_-]+)$', spec)
            if match:
                # <site>:<building>:<units>
                site_name = match.group(1)
                building_name = match.group(2)
                units_spec = match.group(3)
            else:
                match = re.match(r'^(?:(?P<site>\w+):{1,2})?(?P<units>\w+)$', spec)
                if match:
                    # <site>:<units>
                    site_name = match.group('site')
                    units_spec = match.group('units')
                else:
                    logger.error(f"{op}: Invalid units spec: {specifier}")
                    continue


            if site_name:
                if site_name not in [s.name for s in sites]:
                    logger.error(f"{op}: Invalid site: '{site_name}', defined sites: {[s.name for s in sites]}")
                    continue
                else:
                    site = [s for s in sites if s.name == site_name][0]
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
                    units_numbering_base = sum([len(b.units) for b in site.buildings[0:site.buildings.index(building)]])
                    unit_id = str(int(unit) + units_numbering_base)
                    if unit_id not in site.valid_ids:
                        logger.error(f"{op}: {unit_id=} not valid at '{site.name}' ({site.valid_ids=}), skipped.")
                    elif unit_id not in site.deployed_units:
                        logger.error(f"{op}: {unit_id=} not deployed at '{site.name}' ({site.deployed_units=}), skipped.")
                    elif unit_id in site.units_in_maintenance:
                        logger.error(f"{op}: {unit_id=} in maintenance at '{site.name}' ({site.units_in_maintenance=}), skipped.")
                    else:
                        ret.append(f"{site_name}:{unit_id}")

                elif unit in site.valid_ids:
                    unit_id = unit
                    if unit_id not in site.deployed_units:
                        logger.error(f"{op}: {unit_id=} not deployed at '{site.name}' ({site.deployed_units=}), skipped.")
                    elif unit_id in site.units_in_maintenance:
                        logger.error(f"{op}: {unit_id=} in maintenance at '{site.name}' ({site.units_in_maintenance=}), skipped.")
                    else:
                        ret.append(f"{site_name}:{unit_id}")
                else:
                    logger.error(f"{op}: Invalid unit: '{unit}' at '{site.name=}', known units: {site.valid_ids}")

    return ret


def parse_unit_ids(units_spec: str) -> List[str]:
    """
    Parses and validates a units specifier (a string):

    :param units_spec: a units specifier, e.g. "mastw" or "w" or "1-5" or "3,4,2-6"
    :return: list of fully qualified unit names
    """
    ret = []

    for spec in units_spec.split(','):
        if '-' in spec:
            word = spec.split('-')
            if word[0].isdigit() and word[1].isdigit():
                for i in range(int(word[0]), int(word[1])+1):
                    ret.append(str(i))
        else:
            ret.append(spec)

    return ret


def sexagesimal_hours_to_decimal(value: str | float) -> float:
    ret: float = float('NaN')
    try:
        return astropy.coordinates.Longitude(value, unit=u.hourangle)
    except:
        raise


def sexagesimal_degrees_to_decimal(value: str | float) -> float:
    try:
        return astropy.coordinates.Latitude(value, unit=u.deg)
    except:
        raise


if __name__ == '__main__':
    units = parse_units(['w', 'ns:10-12', 'ns:1,3,5', 'ns:south:3-5'])
    print(units)
