"""The idleness fields: the auto-shutdown setting, and what FullUnitStatus publishes.

Design: mast-claude-config plans/unit_idleness_design.md.
"""

from __future__ import annotations

import pytest

from common.config.unit import UnitConfig
from common.models.statuses import FullUnitStatus


class TestTheSetting:
    def test_it_defaults_to_disabled(self):
        assert UnitConfig.model_fields["minutes_to_autoshutdown_on_idle"].default == 0

    @pytest.mark.parametrize("minutes", [0, 5, 30, 600])
    def test_zero_and_five_or_more_are_accepted(self, minutes):
        assert UnitConfig._zero_or_at_least_five(minutes) == minutes

    @pytest.mark.parametrize("minutes", [1, 2, 3, 4])
    def test_one_to_four_are_refused_with_the_rule(self, minutes):
        with pytest.raises(ValueError, match="use 0 to disable, or at least 5"):
            UnitConfig._zero_or_at_least_five(minutes)

    def test_negative_is_refused(self):
        assert UnitConfig.model_fields["minutes_to_autoshutdown_on_idle"].metadata, "ge=0 is declared"


class TestTheStatusFields:
    def test_an_old_unit_reads_as_unknown_not_idle(self):
        """`None` is unknown; `{}` would read as "known and idle" (the design's load-bearing rule)."""
        status = FullUnitStatus.model_validate({"id": 1})

        assert status.why_busy is None
        assert status.idle_seconds is None
        assert status.active_seconds is None

    def test_a_busy_unit_round_trips(self):
        payload = {"id": 1, "why_busy": {"unit": ["Guiding"], "covers": ["Closing"]}, "active_seconds": 12.5}

        status = FullUnitStatus.model_validate(payload)

        assert status.why_busy == {"unit": ["Guiding"], "covers": ["Closing"]}
        assert status.active_seconds == 12.5
        assert status.idle_seconds is None
