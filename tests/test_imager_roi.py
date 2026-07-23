"""Regression tests for ImagerRoi construction paths.

Two contracts live side by side (MAST_common#17 context):

- the default constructor CONDITIONS every rectangle (center-preserving
  shrink to camera alignment constraints) -- pinned here as-is, bugs and all,
  until #17 resolves it;
- ``ImagerRoi.verbatim()`` bypasses conditioning entirely, for consumers
  (PHD2 ``set_limit_frame``) that must receive the rectangle exactly as
  configured -- PHD2 applies the camera constraints itself (upstream PRs
  #1374-#1376).
"""

from __future__ import annotations

import pytest

statuses = pytest.importorskip(
    "common.models.statuses", reason="models package import chain unavailable"
)
from common.models.statuses import ImagerRoi  # noqa: E402

# The PR #29 deployment-example rect: conditioning demonstrably mutates it.
RECT = {"x": 3031, "y": 2692, "width": 2000, "height": 400}
CONDITIONED_RECT = {"x": 3038, "y": 2693, "width": 1984, "height": 396}
# Dimensions no camera readout would accept -- verbatim must not care.
ODD_RECT = {"x": 100, "y": 200, "width": 1999, "height": 401}


def as_tuple(roi: ImagerRoi) -> tuple[int, int, int, int]:
    return (roi.x, roi.y, roi.width, roi.height)


class TestVerbatim:
    @pytest.mark.parametrize("rect", [RECT, ODD_RECT], ids=["deploy-example", "odd-dims"])
    def test_rect_is_untouched(self, rect):
        roi = ImagerRoi.verbatim(**rect)
        assert as_tuple(roi) == (rect["x"], rect["y"], rect["width"], rect["height"])

    def test_bypasses_conditioning_that_would_mutate(self):
        assert as_tuple(ImagerRoi(**RECT)) != as_tuple(ImagerRoi.verbatim(**RECT))

    def test_is_idempotent_through_round_trip(self):
        first = ImagerRoi.verbatim(**RECT)
        second = ImagerRoi.verbatim(
            x=first.x, y=first.y, width=first.width, height=first.height
        )
        assert as_tuple(second) == as_tuple(first)


class TestDefaultConstructorStillConditions:
    def test_known_example_conditioned_form(self):
        """Pin of today's conditioning output (non-idempotence tracked in #17)."""
        roi = ImagerRoi(**RECT)
        assert as_tuple(roi) == (
            CONDITIONED_RECT["x"],
            CONDITIONED_RECT["y"],
            CONDITIONED_RECT["width"],
            CONDITIONED_RECT["height"],
        )
