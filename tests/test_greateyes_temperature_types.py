"""The greateyes temperature set points must survive ``ctypes.c_int``.

``TemperatureControl_SetTemperature`` hands its argument straight to ``ctypes.c_int``, so a
whole degree is the only thing the hardware can be told. A float is not a finer set point --
it is a ``TypeError`` at the SDK call, raised from ``cool_down()``, which runs from
``startup()``. That is a service that will not start, for every deepspec camera at once.

It happened: these fields were ``float``, and MAST_spec did not notice because it read them
through ``GreateyesSettingsModel``, whose ``target_cool`` is an ``int``, so building that
model from this config rounded on the way past. MAST_spec#54 collapsed the two settings
objects into the config one and the rounding went with it.

The test is here rather than in MAST_spec because this is where the type is decided, and
MAST_spec has no test suite to put it in.
"""

from __future__ import annotations

import ctypes

import pytest

config = pytest.importorskip("common.config.greateyes", reason="config import chain unavailable")

from common.config.greateyes import GreateyesTemperatureConfig  # noqa: E402


class TestTheSdkAccceptsThem:
    def test_defaults_reach_the_sdk(self):
        temp = GreateyesTemperatureConfig()
        # The call the service makes at startup. Raises TypeError on a float.
        ctypes.c_int(temp.target_cool)
        ctypes.c_int(temp.target_warm)

    def test_the_values_every_ns_camera_carries(self):
        # -5.0 / 0.0 are what the `sites` document holds for all five deepspec entries, as
        # floats. They must arrive here as ints.
        temp = GreateyesTemperatureConfig(target_cool=-5.0, target_warm=0.0, check_interval=30)
        assert temp.target_cool == -5
        assert temp.target_warm == 0
        assert isinstance(temp.target_cool, int)
        assert isinstance(temp.target_warm, int)
        ctypes.c_int(temp.target_cool)
        ctypes.c_int(temp.target_warm)


class TestItRejectsWhatTheHardwareCannotHonour:
    @pytest.mark.parametrize("field", ["target_cool", "target_warm"])
    def test_a_fractional_set_point_fails_at_config_load(self, field):
        """Better here than three layers down, mid-cooldown, on a running telescope."""
        with pytest.raises(Exception, match="(?i)int"):
            GreateyesTemperatureConfig(**{field: -5.5})
