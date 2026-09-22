"""The one definition of "off target", shared by the two callers that must agree.

`solve_and_correct` uses it to correct the mount; the lock nudge uses it to
re-reference guiding to the same target in the last moment before the fold mirror
hides it. Two copies of this arithmetic would let them disagree silently, which is
why it is here rather than in either of them.
"""

from astropy.coordinates import Angle

from common.interfaces.solving import (
    ARCSEC_PER_DEGREE,
    DEGREES_PER_RA_HOUR,
    SolvingSolution,
    target_offset_arcsec,
)


def coord(ra_hours: float, dec_degs: float):
    """A target, in the shape `solve_and_correct` passes."""
    return type("Coord", (), {"ra": Angle(ra_hours, unit="hour"), "dec": Angle(dec_degs, unit="deg")})()


def solved(ra_hours: float, dec_degs: float) -> SolvingSolution:
    return SolvingSolution(ra_hours=ra_hours, dec_degs=dec_degs, dec_rads=Angle(dec_degs, unit="deg").radian)


class TestTheSign:
    def test_it_is_target_minus_solved(self):
        """The correction to apply, not the error observed."""
        d_ra, d_dec = target_offset_arcsec(coord(12.0, 30.0), solved(11.999, 29.999))
        assert d_ra > 0 and d_dec > 0

    def test_a_target_behind_the_solution_is_negative(self):
        d_ra, d_dec = target_offset_arcsec(coord(12.0, 30.0), solved(12.001, 30.001))
        assert d_ra < 0 and d_dec < 0


class TestTheWrap:
    """A target either side of 0h must subtract the short way round."""

    def test_crossing_zero_hours_forwards(self):
        d_ra, _ = target_offset_arcsec(coord(0.01, 0.0), solved(23.99, 0.0))
        expected = 0.02 * DEGREES_PER_RA_HOUR * ARCSEC_PER_DEGREE
        assert abs(d_ra - expected) < 1e-6

    def test_crossing_zero_hours_backwards(self):
        d_ra, _ = target_offset_arcsec(coord(23.99, 0.0), solved(0.01, 0.0))
        expected = -0.02 * DEGREES_PER_RA_HOUR * ARCSEC_PER_DEGREE
        assert abs(d_ra - expected) < 1e-6

    def test_it_never_reports_nearly_a_whole_turn(self):
        """The failure the wrap exists to prevent: 0.3 degrees read as 359.7.

        0.02 h of RA is 0.3 deg, so the short way round is 1080 arcsec. Without the
        wrap the same pair subtracts to 359.7 deg -- over 1.29 million arcsec, and a
        mount offset that would slew most of the way around the sky.
        """
        d_ra, _ = target_offset_arcsec(coord(0.0, 0.0), solved(23.98, 0.0))
        assert abs(d_ra - 0.3 * ARCSEC_PER_DEGREE) < 1e-6
        assert abs(d_ra) < ARCSEC_PER_DEGREE


class TestTheDeclinationTerm:
    def test_it_is_a_plain_difference(self):
        _, d_dec = target_offset_arcsec(coord(6.0, 45.0), solved(6.0, 44.5))
        assert abs(d_dec - 0.5 * ARCSEC_PER_DEGREE) < 1e-3


class TestNoCosDecFactor:
    """A mount offset wants plain RA; a caller converting to pixels applies cos(dec).

    Pinned because the omission is deliberate and invisible: the same RA offset at
    dec 0 and dec 60 must come back identical, and a helper that quietly scaled one
    of them would break the mount correction rather than the nudge.
    """

    def test_the_same_ra_offset_reads_the_same_at_any_declination(self):
        at_equator, _ = target_offset_arcsec(coord(6.01, 0.0), solved(6.0, 0.0))
        at_sixty, _ = target_offset_arcsec(coord(6.01, 60.0), solved(6.0, 60.0))
        assert abs(at_equator - at_sixty) < 1e-6
