"""The project-standard FITS cards: identity, plan, and only-fresh sky quality."""

from __future__ import annotations

import datetime

import numpy as np
import pytest
from astropy.io import fits

import common.fits_headers as fh
from common.models.latest import (
    LatestGuidingSkyQuality,
    LatestMeasuredSkyQuality,
    LatestSkyQuality,
    SkyQualityWindow,
)

NOW = datetime.datetime(2026, 11, 4, 20, 0, 0, tzinfo=datetime.UTC)


def _iso(seconds_ago: float) -> str:
    return (NOW - datetime.timedelta(seconds=seconds_ago)).isoformat()


def _measured(seconds_ago: float = 600.0, succeeded: bool = True) -> LatestMeasuredSkyQuality:
    m = LatestMeasuredSkyQuality(
        observed_at=_iso(seconds_ago),
        fwhm_arcsec=1.987,
        hfd_arcsec=2.25,
        zero_point_mag=24.1234,
        zero_point_spread_mag=0.04,
        zero_point_stars=17,
        airmass=1.13,
        background_adu=1350.0,
    )
    m.started = _iso(seconds_ago + 10)
    m.ended, m.succeeded = _iso(seconds_ago - 5), succeeded
    return m


def _guiding(last_sample_ago: float = 3.0, running: bool = True) -> LatestGuidingSkyQuality:
    g = LatestGuidingSkyQuality(
        last_sample=_iso(last_sample_ago),
        windows=[
            SkyQualityWindow(
                seconds=s, ends_at=_iso(last_sample_ago), samples=n, hfd_arcsec=h, transparency=t, star_lost=lost
            )
            for s, n, h, t, lost in [(60, 30, 1.9, 0.98, 0), (180, 90, 2.0, 0.95, 1), (300, 150, 2.1, None, 1)]
        ],
    )
    g.started = _iso(1000)
    if not running:
        g.ended = _iso(1)
    return g


@pytest.fixture(autouse=True)
def no_local_config(monkeypatch, tmp_path):
    monkeypatch.setenv("MAST_CONFIG", str(tmp_path / "missing.toml"))
    from common.config import local

    if hasattr(local.load_local_config, "cache_clear"):
        local.load_local_config.cache_clear()


def test_every_keyword_fits_in_eight_characters():
    cards = fh.standard_fits_cards(
        plan_id="01ABC", sky_quality=LatestSkyQuality(measured=_measured(), while_guiding=_guiding()), now=NOW
    )
    assert all(len(k) <= 8 for k in cards), [k for k in cards if len(k) > 8]


def test_the_header_date_and_identity_are_always_there():
    cards = fh.standard_fits_cards(now=NOW)
    assert cards["DATE"][0] == "2026-11-04T20:00:00.000"
    assert cards["ORIGIN"][0] == "MAST"
    assert "TELESCOP" in cards
    assert "PLANID" not in cards and not any(k.startswith("SQ") for k in cards)


def test_a_missing_config_file_limits_identity_and_does_not_raise():
    cards = fh.identity_cards()
    assert set(cards) == {"ORIGIN", "TELESCOP"}


def test_the_plan_is_stamped_when_given():
    assert fh.standard_fits_cards(plan_id="01ABC", now=NOW)["PLANID"][0] == "01ABC"


class TestMeasured:
    def test_a_fresh_frame_is_stamped_with_its_date_and_age(self):
        cards = fh.sky_quality_cards(LatestSkyQuality(measured=_measured(600)), NOW)
        assert cards["SQM-DATE"][0] == "2026-11-04T19:50:00.000"
        assert cards["SQM-AGE"][0] == 600
        assert cards["SQMFWHM"][0] == 1.987
        assert cards["SQMZP"][0] == 24.123
        assert cards["SQMZPN"][0] == 17

    def test_an_hour_old_frame_is_not(self):
        cards = fh.sky_quality_cards(LatestSkyQuality(measured=_measured(fh.MEASURED_MAX_AGE_SECONDS + 1)), NOW)
        assert not cards

    def test_a_failed_measurement_is_not(self):
        assert not fh.sky_quality_cards(LatestSkyQuality(measured=_measured(succeeded=False)), NOW)

    def test_missing_values_are_left_out_not_written_empty(self):
        m = _measured()
        m.zero_point_mag = m.zero_point_spread_mag = None
        m.zero_point_stars = 0
        cards = fh.sky_quality_cards(LatestSkyQuality(measured=m), NOW)
        assert "SQMFWHM" in cards and not {"SQMZP", "SQMZPSD", "SQMZPN"} & set(cards)


class TestGuiding:
    def test_a_running_session_stamps_every_window(self):
        cards = fh.sky_quality_cards(LatestSkyQuality(while_guiding=_guiding()), NOW)
        assert cards["SQG-DATE"][0] == "2026-11-04T19:59:57.000"
        assert (cards["SQG1HFD"][0], cards["SQG3HFD"][0], cards["SQG5HFD"][0]) == (1.9, 2.0, 2.1)
        assert cards["SQG1N"][0] == 30 and cards["SQG3LOS"][0] == 1
        assert "SQG5TRN" not in cards  # no transparency yet for that window

    def test_an_ended_session_is_not(self):
        assert not fh.sky_quality_cards(LatestSkyQuality(while_guiding=_guiding(running=False)), NOW)

    def test_a_stalled_session_is_not(self):
        stalled = _guiding(last_sample_ago=fh.GUIDING_MAX_AGE_SECONDS + 1)
        assert not fh.sky_quality_cards(LatestSkyQuality(while_guiding=stalled), NOW)


class TestWriting:
    def test_apply_sets_value_and_comment(self):
        header = fits.Header()
        fh.apply_cards(header, {"SQMFWHM": (1.9, "[arcsec] x")})
        assert header["SQMFWHM"] == 1.9 and header.comments["SQMFWHM"] == "[arcsec] x"

    def test_stamping_a_file_in_place(self, tmp_path):
        path = tmp_path / "frame.fits"
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.uint16)).writeto(path)

        assert fh.stamp_fits_file(path, fh.standard_fits_cards(plan_id="01ABC", now=NOW))

        header = fits.getheader(path)
        assert header["PLANID"] == "01ABC" and header["ORIGIN"] == "MAST"

    def test_stamping_a_file_that_is_not_there_does_not_raise(self, tmp_path):
        assert fh.stamp_fits_file(tmp_path / "missing.fits", {"ORIGIN": ("MAST", "")}) is False
