"""The mount's settle defaults: present, the old hard-coded values, and optional (an existing document still loads)."""

from config.mount import MountConfig


def test_the_settle_defaults_are_the_values_the_mount_used_to_hard_code():
    conf = MountConfig(ascom_driver="ASCOM.PWI4.Telescope")

    assert (conf.settle_tolerance_arcsec, conf.settle_stable_samples) == (0.5, 2)


def test_a_unit_can_set_its_own():
    conf = MountConfig(ascom_driver="x", settle_tolerance_arcsec=6.0, settle_stable_samples=3)

    assert (conf.settle_tolerance_arcsec, conf.settle_stable_samples) == (6.0, 3)
