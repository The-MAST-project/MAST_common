"""`latest.sky_quality.measured`: the frame a solved focus_on_star takes at best focus."""

from common.models.latest import LatestMeasuredSkyQuality, LatestRecords, LatestSkyQuality


def test_no_measurement_yet_is_none_beside_while_guiding():
    assert LatestSkyQuality().measured is None
    assert LatestRecords().sky_quality is None or LatestRecords().sky_quality.measured is None


def test_a_measurement_round_trips():
    record = LatestMeasuredSkyQuality(
        exposure_seconds=5.0,
        binning=1,
        plate_scale_arcsec=0.2616,
        altitude_degs=62.0,
        airmass=1.132,
        star_mag=11.4,
        fwhm_px=7.6,
        fwhm_arcsec=1.99,
        hfd_px=8.7,
        hfd_arcsec=2.28,
        hfd_over_fwhm=1.14,
        catalog_stars=31,
        detected_stars=24,
        matched_stars=19,
        rotation_degs=91.5,
        mirrored=True,
        zero_point_mag=24.1,
        zero_point_spread_mag=0.05,
        zero_point_stars=17,
        background_adu=1350.0,
    ).begin()
    record.finish(True)

    back = LatestSkyQuality.model_validate(LatestSkyQuality(measured=record).model_dump())

    assert back.measured == record
    assert back.measured.succeeded is True


def test_an_unmatched_field_leaves_the_photometry_empty():
    record = LatestMeasuredSkyQuality(fwhm_arcsec=2.0, catalog_stars=12, detected_stars=3)
    assert record.zero_point_mag is None and record.rotation_degs is None and record.matched_stars == 0
