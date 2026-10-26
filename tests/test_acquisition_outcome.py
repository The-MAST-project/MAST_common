"""The pieces MAST_control's multi-unit acquisition needs from `common`.

Design: mast-claude-config plans/control_acquisition_design.md, section 4.
"""

from __future__ import annotations

import pytest

from common.models.plans import DEFAULT_TIMEOUT_TO_GUIDING, Plan
from common.models.statuses import (
    COOLING_DOWN_REASON,
)
from common.parsers import parse_target


def test_plans_default_to_the_shared_timeout_to_guiding():
    assert Plan.model_fields["timeout_to_guiding"].default == DEFAULT_TIMEOUT_TO_GUIDING == 600


def test_the_cooling_reason_matches_what_units_already_send():
    # Pinned, not merely shared: units deployed before they built their messages from this
    # constant send the literal text, and control must still recognise it.
    assert COOLING_DOWN_REASON == "imager: cooling down"


@pytest.mark.parametrize(
    ("ra", "dec", "expected"),
    [
        ("5.575", "-45.5", (5.575, -45.5)),
        (5.575, -45.5, (5.575, -45.5)),
        ("05:34:30", "+22:00:36", (5.575, 22.01)),
        (" 05 34 30 ", "22 00 36", (5.575, 22.01)),
        ("0", "0", (0.0, 0.0)),
        (0, 0, (0.0, 0.0)),
    ],
)
def test_parse_target_takes_every_accepted_form(ra, dec, expected):
    assert parse_target(ra, dec) == pytest.approx(expected)


@pytest.mark.parametrize("missing", [None, ""])
def test_parse_target_passes_a_missing_coordinate_through(missing):
    assert parse_target(missing, missing) == (None, None)


@pytest.mark.parametrize(("ra", "dec"), [("24", "0"), ("0", "91"), ("abc", "0")])
def test_parse_target_refuses_what_is_out_of_range_or_unparseable(ra, dec):
    with pytest.raises(ValueError):
        parse_target(ra, dec)
