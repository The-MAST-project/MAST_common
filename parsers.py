from typing import List
from common.config import Config
import re

def parse_range(s: str) -> tuple:
    pass

def parse_unit_ids(s: str) -> List[str]:
    pass

def parse_units(specifiers: List[str]) -> tuple:
    """
    Parses and validates unit specifiers.  Valid specifiers:
     - 'w', 'wis::w', 'ns:north:9', 'ns:17'

    :param specifiers: one or more unit specifiers
    :return: on success True, values, on failure False, List[errors], where values is a list of 'site:building:unit-id' triplets
    """
    errors: List[str] = []
    ret: List[str] = []
    sites_cfg = Config().get_sites()
    if isinstance(specifiers, str):
        specifiers = [specifiers]

    for specifier in specifiers:
        building = None
        building_name = None

        match = re.match(r'^(?:(?P<site>\w+):)?(?:(?P<building>\w+):)?(?P<units>[,a-zA-Z0-9_-]+)$', specifier)
        if match:
            site_name = match.group(1)
            building_name = match.group(2)
            units_spec = match.group(3)
        else:
            match = re.match(r'^(?:(?P<site>\w+):{1,2})?(?P<units>\w+)$', specifier)
            if match:
                site_name = match.group('site')
                units_spec = match.group('units')
            else:
                errors.append(f"Invalid units spec: {specifier}")
                continue

        site = None
        if site_name:
            site = [s for s in sites_cfg if s.name == site_name][0]
            if not site:
                errors.append(f"Invalid site: '{site_name}'")
                continue
        else:
            result = [s for s in sites_cfg if hasattr(s, 'local') and s.local == True]
            if result:
                site = result[0]

        if building_name:
            if building_name.isdigit() and int(building_name) in range(0, len(site.buildings)):
                building = site.buildings[int(building_name)]
            else:
                for b in site.buildings:
                    if building_name in b.names:
                        building = b
                        break
            if not building:
                # a building was specified but it's not valid
                errors.append(f"Invalid building: '{building_name}' at site '{site.name}'")
                continue

        units = parse_unit_ids(units_spec)
        for unit in units:
            if building and unit in building.units:
                unit_in_site = site.units_map[f"{building.id}:{unit}"]
                ret.append(f"{site.name}:{unit_in_site}")
            elif unit in site.units:
                ret.append(f"{site.name}:{unit}")
            else:
                errors.append(f"Invalid unit: '{unit}' ({specifier=})")

    return (False, errors) if errors else (True, ret)