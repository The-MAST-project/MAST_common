"""Regression tests for Target's RA/Dec validation (MAST_common#30).

The defect these pin down was invisible: ``validate_ra`` parsed with astropy
``Longitude``, which NORMALISES into [0, 24) before returning, so an RA of 25
became 1.0 -- accepted as a different target -- and the ``0 <= ra < 24`` guard
that followed could never fail. ``validate_dec`` had the mirror problem, with
``Latitude`` raising before its own check was reached. Both checks were dead
code that read as if it worked.

So the tests below are in two halves:

- ACCEPTED pins the input grammar, which deliberately did not change -- no
  coordinate that worked before may stop working (MAST_unit#2 added the
  space-separated form; the real-world corpus comes from the unit logs
  analysed in MAST_unit#88);
- REJECTED pins the checks themselves, and asserts on the returned VALUE for
  the wrap cases, because an implementation that silently wraps also passes a
  test that only asserts "no exception".
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("common.models.targets", reason="models package import chain unavailable")
from pydantic import ValidationError

from common.models.targets import Target

# Fixed stand-ins so a test for one field is never affected by the other.
VALID_RA = "12:00:00"
VALID_DEC = "0:00:00"


def make(**overrides) -> Target:
    return Target(**{"ra_hours": VALID_RA, "dec_degrees": VALID_DEC, **overrides})


def ra(value) -> float:
    return make(ra_hours=value).ra_hours  # type: ignore[return-value]


def dec(value) -> float:
    return make(dec_degrees=value).dec_degrees  # type: ignore[return-value]


def message(excinfo) -> str:
    return excinfo.value.errors()[0]["msg"]


class TestAcceptedForms:
    """The grammar, unchanged. Every case here worked before this PR too."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("5:34:32.5", 5.575694444444444),
            ("05 34 32.5", 5.575694444444444),
            ("5:4:2", 5.067222222222222),
            ("05:04:02", 5.067222222222222),
            ("5.575", 5.575),
            (5.575, 5.575),
        ],
        ids=["colon", "space", "1-digit", "2-digit", "decimal-str", "decimal-float"],
    )
    def test_ra_forms(self, value, expected):
        assert ra(value) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("+22 00 52.5", 22.014583333333334),
            ("-22:00:52.5", -22.014583333333334),
            ("+22.014", 22.014),
            ("-22.014", -22.014),
            (-22.014, -22.014),
        ],
        ids=["plus-space", "minus-colon", "plus-decimal", "minus-decimal", "negative-float"],
    )
    def test_dec_signs(self, value, expected):
        assert dec(value) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "padded",
        ["  22:04:47.93  ", "\t22:04:47.93", "22:04:47.93 ", "\n22:04:47.93\n"],
        ids=["both", "leading-tab", "trailing", "newlines"],
    )
    def test_surrounding_whitespace_is_ignored(self, padded):
        assert ra(padded) == ra(padded.strip())

    @pytest.mark.parametrize(
        "decimals",
        ["5", "9378328000", "123456789012345"],
        ids=["1-decimal", "10-decimals", "15-decimals"],
    )
    def test_any_number_of_decimals(self, decimals):
        """The endpoint regexes cap seconds at 3 decimals; the model must not."""
        assert ra(f"5:34:32.{decimals}") == pytest.approx(5.5757, abs=1e-3)

    @pytest.mark.parametrize(
        ("param", "value"),
        [
            ("ra_hours", "22:04:47.9378328000 "),
            ("ra_hours", "22 04 47.9378328000 "),
            ("ra_hours", "22:04:47.93 "),
            ("ra_hours", "22 04 47.93 "),
            ("ra_hours", "22 05 5.764"),
            ("dec_degrees", "+46:31:51.791897964"),
            ("dec_degrees", "46:31:51.791897964"),
            ("dec_degrees", "+46:34:9.70"),
        ],
    )
    def test_real_inputs_rejected_by_the_endpoint_guards(self, param, value):
        """Every coordinate the unit's FastAPI patterns rejected on 2026-08-04.

        The guards are stricter than the code behind them (MAST_unit#88); the
        model must accept all of these, or fixing the guards will just move the
        failure one layer down.
        """
        assert isinstance(make(**{param: value}).model_dump()[param], float)


class TestRanges:
    @pytest.mark.parametrize("value", [0, 0.0, "0:00:00", 23.999, "23:59:59.999"])
    def test_ra_inside(self, value):
        assert 0 <= ra(value) < 24

    @pytest.mark.parametrize("value", [-90, 90, -90.0, 90.0, "90:00:00", "-90:00:00"])
    def test_dec_inclusive_bounds(self, value):
        assert abs(dec(value)) == 90

    @pytest.mark.parametrize("value", [24, 24.0], ids=["int", "float"])
    def test_ra_upper_bound_is_exclusive(self, value):
        with pytest.raises(ValidationError) as e:
            ra(value)
        assert "out of range" in message(e)

    @pytest.mark.parametrize("value", [91, -91, 90.001, "90:00:00.001"])
    def test_dec_outside(self, value):
        with pytest.raises(ValidationError) as e:
            dec(value)
        assert "out of range" in message(e)


class TestNoWrapping:
    """The actual defect. Asserting only 'it raises' would not catch a regression
    to Longitude, which raises nothing -- it silently returns a different sky
    position. Each case therefore names the value the old code produced."""

    @pytest.mark.parametrize(
        ("value", "silently_became"),
        [(25, 1.0), (-1, 23.0), (25.5, 1.5), ("25.5", 1.5), (48, 0.0)],
    )
    def test_out_of_range_ra_is_rejected_not_wrapped(self, value, silently_became):
        with pytest.raises(ValidationError) as e:
            ra(value)
        # The report must name the value AS GIVEN. Longitude would have returned
        # `silently_became` with no error at all; anything that normalises first
        # would name that instead, and this assertion is what catches it.
        assert f"RA {float(value)} is out of range" in message(e)
        assert float(value) != pytest.approx(silently_became)

    def test_decimal_and_sexagesimal_agree(self):
        """25 and '25:00:00' are the same mistake and must both be refused.

        Before this PR the decimal form was accepted (wrapped to 1.0) while the
        sexagesimal form raised IllegalHourError.
        """
        for form in (25, "25:00:00"):
            with pytest.raises(ValidationError):
                ra(form)


class TestErrorsAreOurs:
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite(self, value):
        """inf parses to nan rather than raising, and nan compares False against
        every bound -- so an unguarded range test calls infinity 'out of range'."""
        with pytest.raises(ValidationError) as e:
            ra(value)
        assert "not a finite number" in message(e)

    @pytest.mark.parametrize("value", ["", "   ", "\t"], ids=["empty", "spaces", "tab"])
    def test_blank(self, value):
        with pytest.raises(ValidationError) as e:
            ra(value)
        assert "is empty" in message(e)

    @pytest.mark.parametrize("value", ["abc", "1e3", "12:aa:00"])
    def test_unparseable_names_the_accepted_forms(self, value):
        with pytest.raises(ValidationError) as e:
            ra(value)
        assert "cannot parse" in message(e)
        assert "sexagesimal" in message(e) and "decimal" in message(e)

    def test_message_identifies_the_field(self):
        with pytest.raises(ValidationError) as e:
            ra(25)
        assert message(e).startswith("Value error, RA")
        with pytest.raises(ValidationError) as e:
            dec(91)
        assert message(e).startswith("Value error, Dec")


class TestReturnedType:
    """Validation normalises to float regardless of the input form, which the
    consumers rely on (unit's acquirer/autofocusing call float() on these)."""

    @pytest.mark.parametrize("value", ["5:34:32.5", "5.575", 5.575, 5])
    def test_ra_is_always_float(self, value):
        result = ra(value)
        assert isinstance(result, float) and math.isfinite(result)
