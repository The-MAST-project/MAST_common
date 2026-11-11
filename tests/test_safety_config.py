"""The `safety` collection: reading it, and turning it into `Safety` sensors."""

import datetime
from dataclasses import replace

import pytest

from common.config import Config
from common.config.local import ConfigError
from common.config.safety import SafetyConfig
from common.config.site import Site
from common.safety import Safety

T0 = datetime.datetime(2026, 11, 11, 20, 0, 0, tzinfo=datetime.UTC)
NOW0 = datetime.timedelta(0)

SITE = {
    "name": "ns",
    "project": "mast",
    "controller_host": "mast-ns-control",
    "spec_host": "mast-ns-spec",
    "unit_ids": ["01"],
    "location": {"latitude": 30.0, "longitude": 35.0},
}
DOC = {
    "site": "ns",
    "sensors": {
        "wind_speed": {"max": 25.0, "settling": 60},
        "rain_rate": {"max": 0.0, "settling": 900},
        "outside_humidity": {"enabled": False, "max": 85.0},
    },
    "sun_elevation": {"enabled": True, "dawn": -9, "dusk": -12},
}


class _Local:
    site = "ns"


def config_with(safety_docs: list[dict] | None) -> Config:
    colls = {"sites": [SITE], "services": [], "specs": [{}], "units": []}
    if safety_docs is not None:
        colls["safety"] = safety_docs
    Config._reset_for_tests(colls)
    cfg = object.__new__(Config)
    cfg.local = _Local()
    return cfg


@pytest.fixture(autouse=True)
def _clean():
    yield
    Config._reset_for_tests()


def test_reads_this_sites_document():
    cfg = config_with([{"site": "elsewhere", "sensors": {}}, DOC])
    safety = cfg.get_safety()
    assert safety is not None
    assert safety.sensors["wind_speed"].max == 25.0
    assert safety.sensors["wind_speed"].enabled is True  # an entry means enabled unless it says otherwise
    assert safety.sensors["outside_humidity"].enabled is False


def test_missing_collection_or_document_is_none():
    assert config_with(None).get_safety() is None  # e.g. a boot cache from before the collection existed
    assert config_with([]).get_safety() is None
    assert config_with([{"site": "elsewhere"}]).get_safety() is None


def test_mistyped_key_is_refused_not_ignored():
    cfg = config_with([{"site": "ns", "sensors": {"wind_speed": {"max": 25, "setling": 60}}}])
    with pytest.raises(ConfigError, match="setling"):
        cfg.get_safety()


@pytest.mark.parametrize("limits", [{"min": 5, "max": 1}, {"settling": -1}])
def test_nonsense_limits_are_refused(limits):
    cfg = config_with([{"site": "ns", "sensors": {"wind_speed": limits}}])
    with pytest.raises(ConfigError):
        cfg.get_safety()


def test_apply_config_builds_sensors_and_sun():
    safety = Safety()
    safety.apply_config(SafetyConfig(**DOC), project="mast", site=Site(**SITE))
    assert set(safety.sensors) == {"wind_speed", "rain_rate", "outside_humidity"}
    assert safety.sensors["rain_rate"].window == datetime.timedelta(minutes=15)
    assert safety.sun is not None
    assert (safety.sun.settings.dawn, safety.sun.settings.dusk) == (-9, -12)


def test_sun_limits_are_required_and_not_taken_from_the_site():
    with pytest.raises(ValueError):
        SafetyConfig(site="ns", sun_elevation={"enabled": True})
    # even when the site carries limits of its own, only the document's count
    safety = Safety()
    safety.apply_config(SafetyConfig(site="ns", sun_elevation={"dawn": -3, "dusk": -4}), project="mast", site=Site(**SITE))
    assert safety.sun is not None
    assert (safety.sun.settings.dawn, safety.sun.settings.dusk) == (-3, -4)


def test_apply_config_keeps_readings_and_rejudges():
    safety = Safety()
    safety.apply_config(SafetyConfig(site="ns", sensors={"wind_speed": {"max": 25.0}}), project="mast")
    safety.ingest("wind_speed", (20.0, NOW0), now=T0)
    assert safety.is_safe(T0)

    safety.apply_config(SafetyConfig(site="ns", sensors={"wind_speed": {"max": 15.0}}), project="mast")
    assert len(safety.sensors["wind_speed"]._readings) == 1  # kept
    assert not safety.is_safe(T0)  # re-judged


def test_apply_config_drops_removed_sensors_and_none_clears_all():
    safety = Safety()
    safety.apply_config(SafetyConfig(**DOC), project="mast", site=Site(**SITE))
    safety.apply_config(SafetyConfig(site="ns", sensors={"wind_speed": {"max": 25.0}}), project="mast")
    assert set(safety.sensors) == {"wind_speed"}
    assert safety.sun is None
    safety.apply_config(None, project="mast")
    assert safety.sensors == {}
    assert safety.is_safe(T0)  # nothing enabled


def test_site_sun_elevation_is_sane():
    site = Site(**SITE)
    noon = datetime.datetime(2026, 6, 21, 9, 0, tzinfo=datetime.UTC)  # ~noon local, mid-latitude
    midnight = datetime.datetime(2026, 6, 20, 21, 0, tzinfo=datetime.UTC)
    assert site.sun_elevation(noon) > 40
    assert site.sun_elevation(midnight) < -20
    with pytest.raises(ValueError):
        Site(**(SITE | {"location": {}})).sun_elevation(noon)


def test_get_sites_fills_sun_limits_from_the_safety_document():
    (site,) = config_with([DOC]).get_sites()
    assert site.location.sun_limits is not None
    assert (site.location.sun_limits.dusk, site.location.sun_limits.dawn) == (-12, -9)


def test_the_safety_document_wins_over_a_stale_sun_limits_in_the_site_document():
    stale = dict(SITE, location={**SITE["location"], "sun_limits": {"dusk": -18, "dawn": -18}})
    colls = {"sites": [stale], "services": [], "specs": [{}], "units": [], "safety": [DOC]}
    Config._reset_for_tests(colls)
    cfg = object.__new__(Config)
    cfg.local = _Local()
    (site,) = cfg.get_sites()
    assert (site.location.sun_limits.dusk, site.location.sun_limits.dawn) == (-12, -9)


@pytest.mark.parametrize("docs", [None, [], [{"site": "ns", "sensors": {}}], [{"site": "ns", "oops": 1}]])
def test_get_sites_without_usable_sun_limits_leaves_them_none(docs):
    (site,) = config_with(docs).get_sites()  # an invalid safety document must not break get_sites
    assert site.location.sun_limits is None


def test_changing_the_safety_document_changes_the_sites():
    cfg = config_with([DOC])
    assert cfg.get_sites()[0].location.sun_limits.dusk == -12
    changed = {**DOC, "sun_elevation": {"enabled": True, "dawn": -9, "dusk": -15}}
    old = Config._snapshot
    Config._snapshot = replace(
        old,
        collections={**old.collections, "safety": [changed]},
        generations={**old.generations, "safety": old.generations["safety"] + 1},
        generation=old.generation + 1,
    )
    assert cfg.get_sites()[0].location.sun_limits.dusk == -15
