"""The pieces of the unit's autostartup that live in `common`: the wait setting, and the
readiness rule the unit's gate and MAST_control share."""

from __future__ import annotations

import pytest
from pydantic import ValidationError, create_model

from common.config.unit import UnitConfig
from common.models.statuses import COOLING_DOWN_REASON, operational_but_for_cooling

#: The real field, on a one-field model: validating a whole UnitConfig needs every section.
_Wait = create_model("_Wait", seconds_to_autostartup=(int, UnitConfig.model_fields["seconds_to_autostartup"]))


class TestTheSetting:
    def test_it_defaults_to_120_seconds(self):
        assert _Wait().seconds_to_autostartup == 120

    @pytest.mark.parametrize("seconds", [1, 120, 600])
    def test_a_positive_wait_is_accepted(self, seconds):
        assert _Wait(seconds_to_autostartup=seconds).seconds_to_autostartup == seconds

    @pytest.mark.parametrize("seconds", [0, -1])
    def test_zero_and_negative_are_refused(self, seconds):
        with pytest.raises(ValidationError):
            _Wait(seconds_to_autostartup=seconds)


class TestTheReadinessRule:
    def test_operational_is_fit(self):
        assert operational_but_for_cooling(True, None)

    def test_only_cooling_is_fit(self):
        reasons = [f"{COOLING_DOWN_REASON}, -5.2 C, set point -10.0 C"]
        assert operational_but_for_cooling(False, reasons)

    def test_cooling_and_something_else_is_not(self):
        assert not operational_but_for_cooling(False, [f"{COOLING_DOWN_REASON} to the set point", "covers: not open"])

    @pytest.mark.parametrize("reasons", [None, []])
    def test_not_operational_with_no_reason_is_not(self, reasons):
        """Nothing says it is only cooling."""
        assert not operational_but_for_cooling(False, reasons)
