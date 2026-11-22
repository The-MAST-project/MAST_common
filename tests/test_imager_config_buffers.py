"""`imager.reuse_frame_buffers`: optional, and off unless a unit turns it on."""

from config.imager import ImagerConfig


def _conf(**extra):
    return ImagerConfig(
        imager_type="zwo",
        valid_imager_types=["zwo"],
        pixel_scale_at_bin1=0.2616,
        format="raw16",
        gain=170,
        **extra,
    )


def test_it_is_off_by_default_so_existing_documents_are_unchanged():
    assert _conf().reuse_frame_buffers is False


def test_a_unit_can_turn_it_on():
    assert _conf(reuse_frame_buffers=True).reuse_frame_buffers is True
