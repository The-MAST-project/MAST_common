"""`set_unit` stores only a unit's real differences from `units.common`.

It diffed the unit's full `model_dump()` -- every defaulted field filled in -- against the
raw `common` document. A field with a model default that `common` does not hold therefore
read as a per-unit difference and was written into the unit's own document. Once there it
wins over `common` in `get_unit`'s merge, so a later change to that default, or a
fleet-wide value set in `common`, silently never reaches the unit.

Found live on 2026-09-24: 8 of 10 unit documents carried `phd2.limit_frame` at exactly its
default, and `autofocus.rois` / `imager.roi` sat at `null` on all 10, while `units.common`
held neither.
"""

from __future__ import annotations

import pytest

pytest.importorskip("common.config.unit", reason="config package import chain unavailable")

from test_config_watch import BASE, FakeConfigSource, make_config  # noqa: E402

from common.config import Config  # noqa: E402
from common.config.phd2 import LimitFrameConfig, LimitFrameMode, PHD2Config  # noqa: E402
from common.config.unit import UnitConfig  # noqa: E402

# The `phd2` section of the real `common` document, which predates `limit_frame`.
COMMON_PHD2 = {
    "profile": "PWI4+ASI-native,binning=1,bpp=16",
    "settle": {"pixels": 1, "time": 0, "timeout": 0},
    "validation_interval": 0.0,
}
COMMON = {"name": "common", "phd2": COMMON_PHD2}


@pytest.fixture(autouse=True)
def _clean():
    yield
    Config._reset_for_tests()


def _config_with_common(common: dict):
    collections = {**{k: [dict(d) for d in v] for k, v in BASE.items()}, "units": [common]}
    source = FakeConfigSource(collections)
    return make_config(source, collections=collections), source


def _unit(phd2: PHD2Config) -> UnitConfig:
    # Only the sections under test: model_construct skips validation, so the other
    # required sections are simply absent from the dump, on both sides of the diff.
    return UnitConfig.model_construct(name="mast01", phd2=phd2)


def test_a_unit_at_its_defaults_writes_nothing():
    cfg, source = _config_with_common(COMMON)

    cfg.set_unit(unit_name="mast01", unit_conf=_unit(PHD2Config.model_validate(COMMON_PHD2)))

    assert source.written == []


def test_a_real_difference_is_written_and_nothing_else():
    cfg, source = _config_with_common(COMMON)
    phd2 = PHD2Config.model_validate(COMMON_PHD2)
    phd2.limit_frame = LimitFrameConfig(mode=LimitFrameMode.FULL_FRAME)

    cfg.set_unit(unit_name="mast01", unit_conf=_unit(phd2))

    assert source.written == [("mast01", {"name": "mast01", "phd2": {"limit_frame": {"mode": "full_frame"}}})]


def test_a_value_common_does_hold_is_still_compared_against_common():
    common = {"name": "common", "phd2": {**COMMON_PHD2, "limit_frame": {"mode": "full_frame"}}}
    cfg, source = _config_with_common(common)

    # The model default (derived) differs from common's full_frame: a genuine override.
    cfg.set_unit(unit_name="mast01", unit_conf=_unit(PHD2Config.model_validate(COMMON_PHD2)))

    assert source.written == [("mast01", {"name": "mast01", "phd2": {"limit_frame": {"mode": "derived"}}})]
