"""Which frame types require a closed shutter.

``CLOSED_SHUTTER_FRAMES`` is the single statement of that, deliberately here rather than in
either camera: the Newton says it with ``SetShutter(mode=2)`` and the greateyes with
``OpenShutter(0)``, and only the mapping to an SDK call is theirs. Before it existed neither
camera consulted the frame type at all -- ``dark`` and ``bias`` were exposed with the shutter
opening exactly as a light frame's does, and the request was recorded only in the filename.

The exhaustiveness test is the one that earns its keep: a new ``FrameType`` member fails it
until someone decides which side of the shutter it falls on, rather than silently defaulting
to open.
"""

from __future__ import annotations

import pytest

spec = pytest.importorskip("common.spec", reason="common.spec import chain unavailable")

from common.spec import (  # noqa: E402
    CLOSED_SHUTTER_FRAMES,
    FrameType,
    integration_duration_for,
)


class TestMembership:
    @pytest.mark.parametrize("frame_type", [FrameType.BIAS, FrameType.DARK])
    def test_dark_frames_close_the_shutter(self, frame_type):
        assert frame_type in CLOSED_SHUTTER_FRAMES

    @pytest.mark.parametrize("frame_type", [FrameType.LIGHT, FrameType.FLAT])
    def test_illuminated_frames_do_not(self, frame_type):
        assert frame_type not in CLOSED_SHUTTER_FRAMES


class TestItCoversEveryFrameType:
    def test_every_member_is_decided(self):
        """No FrameType may be neither illuminated nor closed-shutter.

        This is what makes the set safe to extend. Adding a member without adding it here (or
        deliberately leaving it out) fails, instead of inheriting "shutter open" by accident.
        """
        illuminated = {FrameType.LIGHT, FrameType.FLAT}
        assert illuminated | CLOSED_SHUTTER_FRAMES == set(FrameType)
        assert illuminated & CLOSED_SHUTTER_FRAMES == set()

    def test_it_is_a_set_of_frame_types(self):
        # Not strings: the cameras test membership with an enum member, and a set of str
        # would still work by StrEnum coincidence while comparing the wrong kind of thing.
        assert all(isinstance(member, FrameType) for member in CLOSED_SHUTTER_FRAMES)

    def test_it_cannot_be_mutated_by_a_consumer(self):
        # A plain set is shared mutable state: any importer could add to it and change what
        # every camera in the process believes a dark is.
        assert isinstance(CLOSED_SHUTTER_FRAMES, frozenset)


class TestIntegrationDuration:
    """The other half of what separates a bias from a dark: the integration time."""

    def test_a_bias_integrates_for_nothing(self):
        assert integration_duration_for(FrameType.BIAS, 5.0) == 0.0

    @pytest.mark.parametrize("frame_type", [FrameType.LIGHT, FrameType.DARK, FrameType.FLAT])
    def test_everything_else_keeps_its_duration(self, frame_type):
        assert integration_duration_for(frame_type, 5.0) == 5.0

    def test_a_dark_is_not_shortened(self):
        # The distinction the whole function exists for: a dark IS an integration, of the
        # thermal signal. Only the shutter is shared with a bias.
        assert integration_duration_for(FrameType.DARK, 300.0) == 300.0
        assert FrameType.DARK in CLOSED_SHUTTER_FRAMES

    def test_it_is_idempotent(self):
        # Called at more than one resolution point per camera; applying it twice must not
        # mean anything different from applying it once.
        once = integration_duration_for(FrameType.BIAS, 5.0)
        assert integration_duration_for(FrameType.BIAS, once) == once

    def test_a_zero_request_survives_every_frame_type(self):
        # Zero is a legal request in its own right -- "give me your floor" -- not only
        # something a bias produces.
        for frame_type in FrameType:
            assert integration_duration_for(frame_type, 0.0) == 0.0
