"""Stabilization: windowed sensors stay not safe until a full, gap-free, in-limits window exists."""

import datetime
import threading

import pytest

from common.safety import (
    MAX_GAP,
    STALE_LIMIT,
    MinMaxSettingsModel,
    Safety,
    SunElevationSettingsModel,
    SunElevationState,
)

T0 = datetime.datetime(2026, 11, 11, 20, 0, 0, tzinfo=datetime.UTC)
S = datetime.timedelta(seconds=1)


def settings(**kw) -> MinMaxSettingsModel:
    base = {"enabled": True, "project": "mast", "source": "davis", "station": "davis", "datum": "wind_speed"}
    return MinMaxSettingsModel(**(base | kw))


def feed(safety: Safety, sensor: str, start: int, stop: int, value: float, step: int = 4) -> datetime.datetime:
    """Fresh readings (age 0) every `step` seconds over [start, stop]; returns the last `now`."""
    now = T0 + start * S
    for t in range(start, stop + 1, step):
        now = T0 + t * S
        safety.ingest(sensor, (value, datetime.timedelta(0)), now=now)
    return now


@pytest.fixture
def safety() -> Safety:
    sf = Safety()
    sf.add_sensor("wind_speed", settings(max=25.0, settling=60))
    return sf


def test_no_readings_is_not_safe(safety):
    assert not safety.is_safe(T0)
    assert safety.reasons(T0) == ["wind_speed: no readings yet"]


def test_no_sensors_is_safe():
    assert Safety().is_safe(T0)
    assert Safety().verdict(T0).reasons is None


def test_window_must_fill(safety):
    now = feed(safety, "wind_speed", 0, 40, 10.0)
    assert not safety.is_safe(now)
    assert "window" in safety.reasons(now)[0]
    now = feed(safety, "wind_speed", 44, 64, 10.0)
    assert safety.is_safe(now)


def test_unsafe_reading_blocks_until_it_leaves_the_window(safety):
    feed(safety, "wind_speed", 0, 60, 10.0)
    safety.ingest("wind_speed", (30.0, datetime.timedelta(0)), now=T0 + 64 * S)
    now = feed(safety, "wind_speed", 68, 120, 10.0)
    assert not safety.is_safe(now)
    assert "30 > 25" in safety.reasons(now)[0]
    now = feed(safety, "wind_speed", 124, 130, 10.0)  # 64 s has left the 60 s window
    assert safety.is_safe(now)


def test_limit_is_inclusive(safety):
    now = feed(safety, "wind_speed", 0, 64, 25.0)
    assert safety.is_safe(now)


def test_gap_inside_window_blocks(safety):
    feed(safety, "wind_speed", 0, 20, 10.0)
    now = feed(safety, "wind_speed", 40, 70, 10.0)  # 20 s of silence > MAX_GAP
    assert not safety.is_safe(now)
    assert any("gap of" in r for r in safety.reasons(now))
    now = feed(safety, "wind_speed", 74, 110, 10.0)  # the gap has aged out
    assert safety.is_safe(now)


def test_silence_now_blocks(safety):
    now = feed(safety, "wind_speed", 0, 64, 10.0)
    assert safety.is_safe(now)
    later = now + MAX_GAP + S
    assert not safety.is_safe(later)
    assert "no reading for" in safety.reasons(later)[0]


def test_stale_reading_is_refused_and_explained(safety):
    stale = STALE_LIMIT + S
    assert not safety.ingest("wind_speed", (5.0, stale), now=T0)
    assert "old" in safety.reasons(T0)[0]


def test_future_and_nonfinite_readings_are_refused(safety):
    assert not safety.ingest("wind_speed", (5.0, -S), now=T0)
    assert not safety.ingest("wind_speed", (float("nan"), datetime.timedelta(0)), now=T0)
    assert not safety.is_safe(T0)


def test_failed_read_adds_nothing(safety):
    assert not safety.ingest("wind_speed", None, now=T0)


def test_same_reading_polled_twice_counts_once(safety):
    safety.ingest("wind_speed", (10.0, datetime.timedelta(seconds=1)), now=T0 + 1 * S)
    safety.ingest("wind_speed", (10.0, datetime.timedelta(seconds=6)), now=T0 + 6 * S)  # same sensor timestamp: T0
    assert len(safety.sensors["wind_speed"]._readings) == 1


def test_changed_limit_rejudges_history(safety):
    now = feed(safety, "wind_speed", 0, 64, 20.0)
    assert safety.is_safe(now)
    safety.sensors["wind_speed"].settings = settings(max=15.0, settling=60)
    assert not safety.is_safe(now)


def test_no_window_means_newest_reading_decides():
    sf = Safety()
    sf.add_sensor("rain_rate", settings(max=0.0))
    sf.ingest("rain_rate", (1.0, datetime.timedelta(0)), now=T0)
    assert not sf.is_safe(T0)
    sf.ingest("rain_rate", (0.0, datetime.timedelta(0)), now=T0 + 4 * S)
    assert sf.is_safe(T0 + 4 * S)


def test_disabled_sensor_is_left_out():
    sf = Safety()
    sf.add_sensor("rain_rate", settings(enabled=False, max=0.0))
    assert sf.is_safe(T0)  # disabled sensors are left out, and nothing else is enabled
    sf.add_sensor("wind_speed", settings(max=25.0))
    sf.ingest("wind_speed", (1.0, datetime.timedelta(0)), now=T0)
    assert sf.is_safe(T0)


def sun_settings(**kw) -> SunElevationSettingsModel:
    base = {"enabled": True, "project": "mast", "source": "internal", "station": "site", "datum": "sun_elevation"}
    return SunElevationSettingsModel(**(base | kw))


MINUTE = datetime.timedelta(minutes=1)


def sun_safety(elevation) -> Safety:
    sf = Safety(sun=SunElevationState(sun_settings(dawn=-6.0, dusk=-12.0), elevation))
    sf.add_sensor("wind_speed", settings(max=25.0))
    return sf


def test_sun_uses_the_dusk_limit_while_setting():
    sf = sun_safety(lambda when: -5.0 - (when - T0) / MINUTE)  # falling 1 deg/min: -5 at T0
    sf.ingest("wind_speed", (1.0, datetime.timedelta(0)), now=T0)
    assert "-5.0 > -12 (dusk)" in sf.reasons(T0)[0]


def test_sun_uses_the_dawn_limit_while_rising():
    sf = sun_safety(lambda when: -8.0 + (when - T0) / MINUTE)  # rising 1 deg/min: -8 at T0
    sf.ingest("wind_speed", (1.0, datetime.timedelta(0)), now=T0)
    assert sf.is_safe(T0)  # -8 <= -6 (dawn), though it would fail the dusk limit


def test_sun_with_no_limits_is_not_safe():
    sf = Safety(sun=SunElevationState(sun_settings(), lambda when: -30.0))
    assert "neither dawn nor dusk" in sf.reasons(T0)[0]


def test_sun_computation_failure_is_not_safe():
    def boom(when):
        raise RuntimeError("no ephemeris")

    sf = Safety(sun=SunElevationState(sun_settings(dusk=-12.0), boom))
    assert "cannot compute" in sf.reasons(T0)[0]


def test_verdict_carries_reasons_only_when_unsafe(safety):
    v = safety.verdict(T0)
    assert v.is_safe is False
    assert v.reasons
    now = feed(safety, "wind_speed", 0, 64, 10.0)
    v = safety.verdict(now)
    assert v.is_safe is True
    assert v.reasons is None
    assert "reasons" in v.model_dump() and v.model_dump()["reasons"] is None


def test_concurrent_ingest_and_reads(safety):
    errors: list[Exception] = []

    def writer():
        try:
            for i in range(200):
                safety.ingest("wind_speed", (10.0, datetime.timedelta(0)), now=T0 + i * S)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    def reader():
        try:
            for _ in range(200):
                safety.verdict(T0 + 100 * S)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=f) for f in (writer, reader, reader)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
