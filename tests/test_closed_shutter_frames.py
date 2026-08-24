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

from common.spec import CLOSED_SHUTTER_FRAMES, FrameType  # noqa: E402


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
