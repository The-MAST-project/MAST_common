"""The ``phd2.guide_solve_mask`` columns: sensor x left out of guide-frame plate solves (MAST_unit#30).

Stars near the fold mirror's shadow band are measured displaced away from it, and a solve
that uses them bends the fiber's position (mast01, 2026-10-28). Off unless both edges are
set, so a stored ``phd2`` section without it still parses and solves as before.
"""

from __future__ import annotations

import pytest

pytest.importorskip("common.config.phd2", reason="config package import chain unavailable")
from pydantic import ValidationError  # noqa: E402

from common.config.phd2 import GuideSolveMaskConfig, PHD2Config  # noqa: E402

STORED = {"profile": "mast", "settle": {"pixels": 2, "time": 10, "timeout": 60}, "validation_interval": 0}


def test_a_stored_section_without_it_parses_and_masks_nothing():
    assert PHD2Config.model_validate(STORED).guide_solve_mask.columns is None


def test_both_edges_set_give_the_columns():
    assert GuideSolveMaskConfig(x_from=2000, x_to=5800).columns == (2000, 5800)


@pytest.mark.parametrize(
    "edges", [{"x_from": 5800, "x_to": 2000}, {"x_from": 2000, "x_to": 2000}, {"x_from": -1, "x_to": 10}]
)
def test_a_band_must_run_left_to_right_on_the_sensor(edges):
    with pytest.raises(ValidationError):
        GuideSolveMaskConfig.model_validate(edges)
