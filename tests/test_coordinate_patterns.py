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
    """Two cases where guard and parser deliberately differ. Pinned so that a
    future change to either makes a decision rather than a discovery."""

    def test_guard_is_stricter_about_component_count(self):
        """astropy reads '5:34' as hours and minutes; the guard demands three
        components, so the ambiguous two-component form is unreachable via HTTP."""
        assert parses("ra", "5:34")
        assert not guard("ra", "5:34")

    def test_guard_does_not_enforce_range(self):
        """Range belongs to one layer. A regex bounding 0-23h/0-59m would be
        unreadable, so the guard passes '99:99:99' and the parser refuses it."""
        assert guard("ra", "99:99:99")
        assert not parses("ra", "99:99:99")


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
