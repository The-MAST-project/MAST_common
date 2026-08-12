"""Resolving an object name to J2000.

No network: Sesame and TNS are both replaced. What is pinned is the order of enquiry, the
refusals, the provenance, and the budget -- the things a wrong answer would be silent
about.

The case worth stating plainly: a misresolution is indistinguishable from success at every
layer below this. The mount slews normally, guiding locks, and a spectrum is taken of the
wrong object. So "which service answered" is not decoration, and neither is refusing a
name that cannot have a fixed position.
"""

from __future__ import annotations

import time

import pytest
from astropy.coordinates import name_resolve

from common import object_resolver as res
from common.object_resolver import (
    MovingTargetError,
    ObjectNameError,
    ResolvedObject,
    is_tns_name,
    resolve_object_name,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    res._cache.clear()
    yield
    res._cache.clear()


@pytest.fixture
def sesame(monkeypatch):
    """Replace Sesame. Records what it was asked; answers unless told to fail."""

    calls: list[str] = []

    class Fake:
        answer: tuple[float, float] | None = (14.06, 54.31)  # ra hours, dec degrees
        fail_with: Exception | None = None

        def __call__(self, name, timeout):
            calls.append(name)
            if self.fail_with is not None:
                raise self.fail_with
            ra, dec = self.answer
            return ResolvedObject(name=name, ra_j2000_hours=ra, dec_j2000_degs=dec, resolver="sesame")

    fake = Fake()
    fake.calls = calls
    monkeypatch.setattr(res, "_resolve_via_sesame", fake)
    return fake


@pytest.fixture
def tns(monkeypatch):
    """Replace TNS and give it credentials, so the TNS arm is reachable."""

    calls: list[str] = []

    class Fake:
        answer: ResolvedObject | None = None
        fail_with: Exception | None = None

        def __call__(self, name, credentials, timeout):
            calls.append(name)
            if self.fail_with is not None:
                raise self.fail_with
            return self.answer

    fake = Fake()
    fake.calls = calls
    monkeypatch.setattr(res, "_resolve_via_tns", fake)
    monkeypatch.setattr(res, "_tns_credentials", lambda: ("key", "1234", "MAST_bot"))
    return fake


class TestMovingTargetsAreRefused:
    """No fixed J2000 exists for these, so a plausible answer is worse than none."""

    @pytest.mark.parametrize(
        "name",
        ["C/2023 A3", "1P/Halley", "73P-C", "2024 AB", "2024 AB1", "(433)", "(433) Eros", "Mars", "ceres", "Io"],
        ids=lambda n: n,
    )
    def test_refused_by_name(self, name, sesame):
        with pytest.raises(MovingTargetError):
            resolve_object_name(name)
        assert sesame.calls == [], "a moving target must not reach a catalogue at all"

    @pytest.mark.parametrize("name", ["M31", "NGC 224", "SN2023ixf", "Gaia DR3 12345", "3C 273"], ids=lambda n: n)
    def test_real_objects_are_not_mistaken_for_moving_ones(self, name, sesame, tns):
        """A false positive here refuses a real target, which is worse than falling
        through to a catalogue that simply will not find something."""
        resolve_object_name(name)


class TestOrderOfEnquiry:
    def test_a_tns_name_asks_tns_first(self, sesame, tns):
        tns.answer = ResolvedObject("SN2023ixf", 14.06, 54.31, resolver="tns", canonical_name="SN 2023ixf")

        result = resolve_object_name("SN2023ixf")

        assert tns.calls == ["SN2023ixf"]
        assert sesame.calls == [], "Sesame must not be asked once TNS has answered"
        assert result.resolver == "tns"

    def test_a_tns_miss_falls_through_to_sesame(self, sesame, tns):
        """A name that merely looks like a transient may be an older object Sesame knows.
        Falling through is safe: Sesame's failure for a fresh transient is 'not found'."""
        tns.answer = None

        result = resolve_object_name("SN1987A")

        assert tns.calls and sesame.calls == ["SN1987A"]
        assert result.resolver == "sesame"

    def test_a_tns_outage_falls_through_rather_than_failing(self, sesame, tns):
        tns.fail_with = RuntimeError("TNS unreachable")

        assert resolve_object_name("AT2024abc").resolver == "sesame"

    def test_missing_tns_credentials_fall_through(self, sesame, monkeypatch):
        """Without a key the resolver is still useful; it is not a reason to refuse."""
        monkeypatch.setattr(res, "_tns_credentials", lambda: None)

        assert resolve_object_name("SN2023ixf").resolver == "sesame"

    def test_a_non_tns_name_never_asks_tns(self, sesame, tns):
        resolve_object_name("M31")
        assert tns.calls == []

    @pytest.mark.parametrize(
        ("name", "expected"),
        [("SN2023ixf", True), ("AT2024abc", True), ("SN 1987A", True), ("M31", False), ("NGC 224", False)],
        ids=lambda v: str(v),
    )
    def test_tns_shape_detection(self, name, expected):
        assert is_tns_name(name) is expected


class TestProvenance:
    def test_the_result_says_who_answered(self, sesame, tns):
        tns.answer = ResolvedObject("SN2023ixf", 14.06, 54.31, resolver="tns", canonical_name="SN 2023ixf")
        assert resolve_object_name("SN2023ixf").resolver == "tns"
        res._cache.clear()
        tns.answer = None
        assert resolve_object_name("SN2023ixf").resolver == "sesame"

    def test_coordinates_are_j2000_hours_and_degrees(self, sesame):
        result = resolve_object_name("M31")
        assert result.ra_j2000_hours == 14.06 and result.dec_j2000_degs == 54.31

    def test_the_result_is_serialisable(self, sesame):
        d = resolve_object_name("M31").as_dict()
        assert set(d) >= {"name", "ra_j2000_hours", "dec_j2000_degs", "resolver", "canonical_name"}


class TestFailure:
    def test_an_unresolvable_name_raises(self, sesame):
        sesame.fail_with = name_resolve.NameResolveError("no match")
        with pytest.raises(ObjectNameError, match="could not be resolved"):
            resolve_object_name("Zaphod Beeblebrox")

    def test_the_error_names_what_was_tried(self, sesame, tns):
        tns.answer = None
        sesame.fail_with = name_resolve.NameResolveError("no match")
        with pytest.raises(ObjectNameError) as excinfo:
            resolve_object_name("SN2099zzz")
        assert "tns" in str(excinfo.value) and "sesame" in str(excinfo.value)

    def test_an_unexpected_error_does_not_escape(self, sesame):
        """A resolver must not surprise its caller with an arbitrary exception; a plan
        sweeper handling ObjectNameError should not also have to handle httpx."""
        sesame.fail_with = ValueError("something odd")
        with pytest.raises(ObjectNameError):
            resolve_object_name("M31")

    @pytest.mark.parametrize("name", ["", "   ", None], ids=["empty", "blank", "none"])
    def test_no_name_is_an_error_not_a_lookup(self, name, sesame):
        with pytest.raises(ObjectNameError):
            resolve_object_name(name)
        assert sesame.calls == []


class TestCache:
    def test_a_second_ask_does_not_reach_the_service(self, sesame):
        resolve_object_name("M31")
        resolve_object_name("M31")
        assert sesame.calls == ["M31"], "the second answer must come from the cache"

    def test_lookup_is_insensitive_to_case_and_spacing(self, sesame):
        resolve_object_name("M31")
        resolve_object_name("  m31 ")
        assert sesame.calls == ["M31"]

    def test_a_definitive_miss_is_remembered_briefly(self, sesame):
        """So a sweeper does not re-ask CDS on every pass for a name that will not
        resolve -- but only briefly, or a transient reported minutes from now stays
        unresolvable for as long as the miss is held."""
        sesame.fail_with = name_resolve.NameResolveError(
            "Unable to find coordinates for name 'Nothing Here' using https://cds.unistra.fr/..."
        )
        with pytest.raises(ObjectNameError):
            resolve_object_name("Nothing Here")
        with pytest.raises(ObjectNameError, match="remembered"):
            resolve_object_name("Nothing Here")
        assert len(sesame.calls) == 1

    def test_a_service_outage_is_NOT_remembered(self, sesame):
        """The bug this exists to prevent, seen live on 2026-08-12: CDS refused a query
        after several in quick succession, `NGC 224` -- which is M31 -- came back
        unresolved, and the miss was cached. It resolved in 0.83s on the next attempt.

        astropy reports both cases as NameResolveError; only the message distinguishes
        them. A service that did not answer says nothing about whether the object exists.
        """
        sesame.fail_with = name_resolve.NameResolveError(
            "All Sesame queries failed. Unable to retrieve coordinates. See errors per URL below:"
        )
        with pytest.raises(ObjectNameError):
            resolve_object_name("NGC 224")

        sesame.fail_with = None
        assert resolve_object_name("NGC 224").resolver == "sesame", "must be retried, not remembered"
        assert len(sesame.calls) == 2

    def test_an_unexpected_error_is_not_remembered_either(self, sesame):
        sesame.fail_with = ValueError("something odd")
        with pytest.raises(ObjectNameError):
            resolve_object_name("M31")
        sesame.fail_with = None
        assert resolve_object_name("M31").resolver == "sesame"
        assert len(sesame.calls) == 2

    def test_a_negative_entry_expires_sooner_than_a_positive_one(self):
        assert res.NEGATIVE_TTL_SECONDS < res.TNS_TTL_SECONDS < res.CATALOGUE_TTL_SECONDS

    def test_an_expired_entry_is_asked_again(self, sesame, monkeypatch):
        resolve_object_name("M31")
        monkeypatch.setattr(time, "monotonic", lambda: time.perf_counter() + res.CATALOGUE_TTL_SECONDS + 1)
        resolve_object_name("M31")
        assert len(sesame.calls) == 2


class TestBudget:
    def test_sesame_is_given_less_than_its_own_default(self):
        """astropy tries its mirrors in turn and `remote_timeout` is per REQUEST, so its
        10s default would make Sesame alone a 20s worst case -- past the whole budget."""
        assert res.SESAME_TIMEOUT_SECONDS * 2 + res.TNS_TIMEOUT_SECONDS <= res.TOTAL_TIMEOUT_SECONDS

    def test_a_slow_tns_does_not_borrow_sesame_time(self, sesame, tns, monkeypatch):
        """The budget is a deadline, not a sum: whatever TNS spends is gone.

        A clock that actually advances, not a frozen one -- freezing `monotonic` moves the
        deadline along with `now`, so nothing ever expires and the test passes vacuously.
        """

        class Clock:
            def __init__(self):
                self.t = 1000.0

            def __call__(self):
                return self.t

        clock = Clock()
        monkeypatch.setattr(time, "monotonic", clock)

        def slow_tns(name, credentials, timeout):
            clock.t += 99  # TNS burned the whole budget
            return None

        monkeypatch.setattr(res, "_resolve_via_tns", slow_tns)

        with pytest.raises(ObjectNameError, match="not resolved within"):
            resolve_object_name("SN2024zzz")
        assert sesame.calls == [], "Sesame must not be asked after the deadline has passed"
