"""The FastAPI guard patterns and the parsers they guard must agree (MAST_unit#88).

MAST_unit carried two divergent copies of these patterns -- one allowing ':' or a
space between components, one only ':' -- and both were stricter than the parsers
sitting behind them: '$'-anchored so a trailing space was fatal, seconds capped at
three decimals, minutes and seconds forced to two digits. Every coordinate error in
the unit logs of 2026-08-04 was the guard rejecting input the parser was written to
accept. Keeping the pattern beside the parser is only half the fix; this pins the
other half, that the two say the same thing.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("common.parsers", reason="config/import chain unavailable")
from common.parsers import (
    DEC_PATTERN,
    RA_PATTERN,
    sexagesimal_degrees_to_decimal,
    sexagesimal_hours_to_decimal,
)

# Every coordinate the unit's old patterns rejected on 2026-08-04, from the logs.
# The guard must accept all of them, and the parser must then make sense of them.
REJECTED_IN_PRODUCTION = [
    ("ra", "22:04:47.9378328000 ", 22.079982731333335),
    ("ra", "22 04 47.9378328000 ", 22.079982731333335),
    ("ra", "22:04:47.93 ", 22.079980555555554),
    ("ra", "22 04 47.93 ", 22.079980555555554),
    ("ra", "22 05 5.764", 22.084934444444443),
    ("dec", "+46:31:51.791897964", 46.531053304989996),
    ("dec", "46:31:51.791897964", 46.531053304989996),
    ("dec", "+46:34:9.70", 46.569361111111114),
]


def guard(kind: str, value: str) -> bool:
    return re.match(RA_PATTERN if kind == "ra" else DEC_PATTERN, value) is not None


def parse(kind: str, value):
    fn = sexagesimal_hours_to_decimal if kind == "ra" else sexagesimal_degrees_to_decimal
    return fn(value)


def parses(kind: str, value) -> bool:
    try:
        parse(kind, value)
    except ValueError:
        return False
    return True


class TestProductionCorpus:
    @pytest.mark.parametrize(("kind", "value", "expected"), REJECTED_IN_PRODUCTION)
    def test_guard_accepts(self, kind, value, expected):
        assert guard(kind, value)

    @pytest.mark.parametrize(("kind", "value", "expected"), REJECTED_IN_PRODUCTION)
    def test_parser_agrees(self, kind, value, expected):
        assert parse(kind, value) == pytest.approx(expected)


class TestGrammar:
    @pytest.mark.parametrize(
        "value",
        [
            "5:34:32.5",  # colon
            "05 34 32.5",  # space
            "5:4:2",  # one digit per component
            "05:04:02",  # two digits
            "5:34:32.123456789",  # any number of decimals
            "  22:04:47.93  ",  # surrounding whitespace
            "5.575",  # decimal
            "0",
            "23:59:59.999",
        ],
    )
    def test_ra_accepted_by_both(self, value):
        assert guard("ra", value), "guard rejected what the parser accepts"
        assert parses("ra", value)

    @pytest.mark.parametrize("value", ["+22 00 52.5", "-22:00:52.5", "+22.014", "-22.014", "-90:00:00"])
    def test_dec_signs_accepted_by_both(self, value):
        assert guard("dec", value)
        assert parses("dec", value)

    @pytest.mark.parametrize("value", ["abc", "", "   ", "12:aa:00", "1e3", "5,34,32"])
    def test_rejected_by_both(self, value):
        assert not guard("ra", value)
        assert not parses("ra", value)

    def test_ra_takes_no_sign(self):
        """RA has no sign, and the previous patterns allowed none either."""
        assert not guard("ra", "-5:34:32")


class TestKnownAsymmetries:
    """Where guard and parser deliberately differ. Pinned so that a future change to
    either makes a decision rather than a discovery."""

    def test_guard_does_not_enforce_range(self):
        """Range belongs to one layer. A regex bounding 0-23h/0-59m would be
        unreadable, so the guard passes '99:99:99' and the parser refuses it."""
        assert guard("ra", "99:99:99")
        assert not parses("ra", "99:99:99")


class TestExplicitRange:
    """Minutes and seconds in [0, 60), the total in its bounds -- all checked by us.
    astropy rolled '5:60:00' over to 6.0 with a warning and never range-checked the
    total, so none of these were refused by it."""

    @pytest.mark.parametrize("value", ["5:60:00", "5:00:60", "5:59:60", "5:75:00", "05 60 00", "5:00:60.0"])
    def test_ra_component_60_or_more_is_rejected_not_rolled_over(self, value):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_hours_to_decimal(value)

    @pytest.mark.parametrize("value", ["45:60:00", "45:00:60", "-45:60:00", "+45:59:60.5"])
    def test_dec_component_60_or_more_is_rejected_not_rolled_over(self, value):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_degrees_to_decimal(value)

    @pytest.mark.parametrize("value", ["24:00:00", "24", "24.0", "23:60:00"])
    def test_ra_24_hours_is_out_of_range(self, value):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_hours_to_decimal(value)

    def test_dec_just_past_the_pole(self):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_degrees_to_decimal("90:00:01")

    def test_boundaries_that_are_inside(self):
        assert sexagesimal_hours_to_decimal("23:59:59.999") < 24
        assert sexagesimal_hours_to_decimal("0:0:0") == 0
        assert sexagesimal_degrees_to_decimal("90:00:00") == 90
        assert sexagesimal_degrees_to_decimal("-90:00:00") == -90
        assert sexagesimal_hours_to_decimal("5:59:59.999") == pytest.approx(5.99999972)

    def test_negative_zero_degrees_keeps_its_sign(self):
        """'-0:30:00' is -0.5, not +0.5: the sign belongs to the whole value."""
        assert sexagesimal_degrees_to_decimal("-0:30:00") == pytest.approx(-0.5)
        assert sexagesimal_degrees_to_decimal("-0 30 00") == pytest.approx(-0.5)

    @pytest.mark.parametrize("value", ["5:34", "5:34:32:1", "5.", ".5", "5:34:32.", "inf", "nan", "1e3"])
    def test_only_the_documented_forms_are_accepted(self, value):
        """No component-count guessing and no unit suffixes: what astropy tolerated
        beyond the grammar is refused, and by the guard as well as the parser."""
        assert not guard("ra", value)
        assert not parses("ra", value)

    @pytest.mark.parametrize("value", [float("inf"), float("nan"), "abc", None, [5]])
    def test_non_coordinates_are_rejected(self, value):
        assert not parses("ra", value)
        assert not parses("dec", value)

    @pytest.mark.parametrize("value", [" 5:34:32.5", "5:34:32.5 ", "\t5:34:32.5\n", "  5.575  "])
    def test_leading_and_trailing_whitespace_is_ignored(self, value):
        assert guard("ra", value)
        assert parses("ra", value)

    @pytest.mark.parametrize("value", ["5  34 32", "5: 34:32", "5 :34:32", "5:34 :32"])
    def test_internal_whitespace_is_not(self, value):
        assert not guard("ra", value)
        assert not parses("ra", value)


class TestNoMixedSeparators:
    """One separator style per value: all colons or all single spaces."""

    @pytest.mark.parametrize("value", ["5:34 32.5", "5 34:32.5", "05:34 32", "05 34:32"])
    def test_mixed_is_rejected_by_both(self, value):
        assert not guard("ra", value)
        assert not parses("ra", value)

    @pytest.mark.parametrize("value", ["-22:00 52.5", "+22 00:52.5"])
    def test_mixed_dec_is_rejected_by_both(self, value):
        assert not guard("dec", value)
        assert not parses("dec", value)

    @pytest.mark.parametrize("value", ["5:34:32.5", "5 34 32.5"])
    def test_uniform_is_accepted(self, value):
        assert guard("ra", value)
        assert parses("ra", value)


class TestLetteredForm:
    """`5h34m32.5s` for RA, `+22d00m52.5s` for Dec: all lower or all upper case."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("5h34m32.5s", 5.575694444444444), ("5H34M32.5S", 5.575694444444444), ("05h04m02s", 5.067222222222222)],
    )
    def test_ra_accepted_by_both(self, value, expected):
        assert guard("ra", value)
        assert parse("ra", value) == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("+22d00m52.5s", 22.014583333333334), ("-22D00M52.5S", -22.014583333333334), ("-0d30m00s", -0.5)],
    )
    def test_dec_accepted_by_both(self, value, expected):
        assert guard("dec", value)
        assert parse("dec", value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", ["5H34m32s", "5h34M32s", "5h34m32S", "5H34M32s", "5h34m", "5h 34m 32s", "5h34m32"])
    def test_mixed_case_and_incomplete_are_rejected_by_both(self, value):
        assert not guard("ra", value)
        assert not parses("ra", value)

    @pytest.mark.parametrize("value", ["+22d00M52s", "22d00m52", "22d 00m 52s"])
    def test_dec_mixed_case_and_incomplete_are_rejected_by_both(self, value):
        assert not guard("dec", value)
        assert not parses("dec", value)

    def test_the_letter_belongs_to_the_field(self):
        """RA takes h, Dec takes d. A Dec typed into the RA field is a mistake, not a coordinate."""
        for value in ("22d00m52s", "22D00M52S"):
            assert not guard("ra", value)
            assert not parses("ra", value)
        for value in ("22h00m52s", "22H00M52S"):
            assert not guard("dec", value)
            assert not parses("dec", value)

    @pytest.mark.parametrize("value", ["5h60m00s", "5h00m60s", "24h00m00s", "5H60M00S"])
    def test_range_still_applies(self, value):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_hours_to_decimal(value)

    def test_dec_range_still_applies(self):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_degrees_to_decimal("90d00m01s")


class TestTargetUsesTheSharedGrammar:
    """Target.ui.pattern is what the GUI compiles as a JS RegExp; it must be the same
    string the FastAPI guards use, not a hand-maintained copy."""

    def test_ui_patterns_are_the_shared_ones(self):
        pytest.importorskip("common.models.targets", reason="config/import chain unavailable")
        from common.models.targets import Target

        props = Target.model_json_schema()["properties"]
        assert props["ra_hours"]["ui"]["pattern"] == RA_PATTERN
        assert props["dec_degrees"]["ui"]["pattern"] == DEC_PATTERN

    @pytest.mark.parametrize(("kind", "value", "expected"), REJECTED_IN_PRODUCTION)
    def test_target_accepts_the_production_corpus(self, kind, value, expected):
        pytest.importorskip("common.models.targets", reason="config/import chain unavailable")
        from common.models.targets import Target

        field = "ra_hours" if kind == "ra" else "dec_degrees"
        other = {"dec_degrees": "0"} if kind == "ra" else {"ra_hours": "0"}
        assert getattr(Target(**{field: value, **other}), field) == pytest.approx(expected)

    def test_target_refuses_what_the_parser_refuses(self):
        pytest.importorskip("common.models.targets", reason="config/import chain unavailable")
        from pydantic import ValidationError

        from common.models.targets import Target

        for ra in ("5:60:00", "24:00:00", "99:99:99"):
            with pytest.raises(ValidationError):
                Target(ra_hours=ra, dec_degrees="0")


class TestNoWrapping:
    """The unit endpoints call these parsers directly, not through Target, so the
    wrap fixed in MAST_common#30 was still live for every /start_acquisition_*
    call until now. Longitude returned 1.0 for an RA of 25 and raised nothing."""

    @pytest.mark.parametrize(("value", "silently_became"), [(25, 1.0), ("25", 1.0), (-1, 23.0), ("25.5", 1.5)])
    def test_ra_out_of_range_rejected_not_wrapped(self, value, silently_became):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_hours_to_decimal(value)

    @pytest.mark.parametrize("value", [91, -91, "90:00:00.001"])
    def test_dec_out_of_range(self, value):
        with pytest.raises(ValueError, match="out of range"):
            sexagesimal_degrees_to_decimal(value)

    @pytest.mark.parametrize("value", [0, 23.999, "23:59:59.999"])
    def test_ra_inside_range_survives(self, value):
        assert 0 <= sexagesimal_hours_to_decimal(value) < 24


class TestRejectionIsLogged:
    """MAST_unit#88's second bullet: a refused coordinate must leave a trace, or
    the next investigation starts from an HTTP status code and nothing else."""

    @pytest.mark.parametrize(("kind", "value"), [("ra", "abc"), ("ra", 25), ("dec", 91), ("ra", "")])
    def test_logs_on_rejection(self, kind, value, caplog):
        with caplog.at_level("ERROR", logger="mast.common.parsers"), pytest.raises(ValueError):
            parse(kind, value)
        assert caplog.records, "rejection was silent"


class TestJavaScriptAgrees:
    """The GUI's Alpine validation does `new RegExp(field.pattern).test(value)`
    (gui/static/js/form_utils.js). Python `re` and JS `RegExp` are different engines,
    so run the same pattern strings, over the same inputs, through Node and require
    the same verdict. Skipped where Node is not installed."""

    INPUTS = (
        [v for _, v, _ in REJECTED_IN_PRODUCTION]
        + ["5:34:32.5", "05 34 32.5", "5:4:2", "5.575", "0", "23:59:59.999", "  22:04:47.93  ", "\t5.5\n"]
        + ["+22 00 52.5", "-22:00:52.5", "-22.014", "-90:00:00", "-5:34:32", "99:99:99", "5:60:00", "24"]
        + ["5:34 32.5", "5 34:32.5", "-22:00 52.5", "abc", "", "   ", "12:aa:00", "1e3", "5,34,32"]
        + ["5:34", "5.", ".5", "5  34 32", "5: 34:32"]
        + ["5h34m32s", "5H34M32.5S", "5H34m32s", "5h34m", "5h 34m 32s", "22d00m52s", "+22d00m52.5s", "-22D00M52S"]
        + ["22h00m52s", "5d34m32s", "+22d00M52s"]
    )

    def test_same_verdict_as_python(self):
        import json
        import shutil
        import subprocess

        node = shutil.which("node")
        if node is None:
            pytest.skip("node is not installed")

        script = (
            "const {ra, dec, inputs} = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "const r = new RegExp(ra), d = new RegExp(dec);"
            "console.log(JSON.stringify(inputs.map(v => [r.test(v), d.test(v)])));"
        )
        payload = json.dumps({"ra": RA_PATTERN, "dec": DEC_PATTERN, "inputs": self.INPUTS})
        out = subprocess.run([node, "-e", script], input=payload, capture_output=True, text=True, check=True, timeout=30)
        js = json.loads(out.stdout)

        for value, (js_ra, js_dec) in zip(self.INPUTS, js, strict=True):
            assert js_ra == guard("ra", value), f"RA pattern disagrees on {value!r}"
            assert js_dec == guard("dec", value), f"Dec pattern disagrees on {value!r}"
