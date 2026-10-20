"""Regression tests for ImagerRoi construction paths.

Two contracts live side by side:

- the default constructor CONDITIONS every rectangle (center-preserving
  shrink to camera alignment constraints), and the conditioning is a
  projection: a legal rect passes through, and conditioning its own output
  changes nothing (MAST_common#17);
- ``ImagerRoi.verbatim()`` bypasses conditioning entirely, for consumers
  (PHD2 ``set_limit_frame``) that must receive the rectangle exactly as
  configured -- PHD2 applies the camera constraints itself (upstream PRs
  #1374-#1376).
"""

from __future__ import annotations

import random

import pytest

statuses = pytest.importorskip("common.models.statuses", reason="models package import chain unavailable")
from common.models.statuses import ImagerRoi  # noqa: E402
from common.rois import SpecRoi, UnitRoi  # noqa: E402

# The PR #29 deployment-example rect, already legal.
RECT = {"x": 3031, "y": 2692, "width": 2000, "height": 400}
# Dimensions no camera readout would accept -- verbatim must not care.
ODD_RECT = {"x": 100, "y": 200, "width": 1999, "height": 401}
ODD_RECT_CONDITIONED = (107, 200, 1984, 400)

FULL_SENSOR = (0, 0, 8288, 5644)
WIDTH_ALIGNMENT = 16
HEIGHT_ALIGNMENT = 4
RANDOM_SEED = 17
RANDOM_SAMPLES = 2000


def as_tuple(roi: ImagerRoi) -> tuple[int, int, int, int]:
    return (roi.x, roi.y, roi.width, roi.height)


class TestVerbatim:
    @pytest.mark.parametrize("rect", [RECT, ODD_RECT], ids=["deploy-example", "odd-dims"])
    def test_rect_is_untouched(self, rect):
        roi = ImagerRoi.verbatim(**rect)
        assert as_tuple(roi) == (rect["x"], rect["y"], rect["width"], rect["height"])

    def test_bypasses_conditioning_that_would_mutate(self):
        assert as_tuple(ImagerRoi(**ODD_RECT)) != as_tuple(ImagerRoi.verbatim(**ODD_RECT))

    def test_is_idempotent_through_round_trip(self):
        first = ImagerRoi.verbatim(**RECT)
        second = ImagerRoi.verbatim(x=first.x, y=first.y, width=first.width, height=first.height)
        assert as_tuple(second) == as_tuple(first)


def center_of(roi: ImagerRoi) -> tuple[int, int]:
    assert roi._center is not None
    return (roi._center.x, roi._center.y)


def random_rects() -> list[tuple[int, int, int, int]]:
    rng = random.Random(RANDOM_SEED)
    return [
        (rng.randint(0, 8000), rng.randint(0, 5000), rng.randint(1, FULL_SENSOR[2]), rng.randint(1, FULL_SENSOR[3]))
        for _ in range(RANDOM_SAMPLES)
    ]


def condition(rect: tuple[int, int, int, int]) -> ImagerRoi:
    x, y, width, height = rect
    return ImagerRoi(x=x, y=y, width=width, height=height)


class TestConditioning:
    @pytest.mark.parametrize(
        "rect",
        [FULL_SENSOR, (RECT["x"], RECT["y"], RECT["width"], RECT["height"]), (7, 1, 8272, 5640), (0, 0, 16, 4)],
        ids=["full-sensor", "deploy-example", "ratcheted-full-frame", "smallest"],
    )
    def test_legal_rect_passes_through(self, rect):
        assert as_tuple(condition(rect)) == rect

    def test_illegal_rect_is_conditioned(self):
        assert as_tuple(ImagerRoi(**ODD_RECT)) == ODD_RECT_CONDITIONED

    def test_is_idempotent(self):
        for rect in random_rects():
            once = condition(rect)
            assert as_tuple(condition(as_tuple(once))) == as_tuple(once), rect

    def test_output_meets_constraints_and_keeps_center(self):
        for rect in random_rects():
            x, y, width, height = rect
            roi = condition(rect)
            assert roi.width % WIDTH_ALIGNMENT == 0, rect
            assert roi.height % HEIGHT_ALIGNMENT == 0, rect
            assert center_of(roi) == (x + width // 2, y + height // 2), rect
            assert center_of(roi) == (roi.x + roi.width // 2, roi.y + roi.height // 2), rect

    def test_survives_revalidation(self):
        roi = ImagerRoi(**ODD_RECT)
        again = ImagerRoi(**roi.model_dump())
        assert as_tuple(again) == as_tuple(roi)
        assert center_of(again) == center_of(roi)


class TestFromOther:
    def test_full_frame_unit_roi_is_unchanged(self):
        roi = ImagerRoi.from_other(UnitRoi(4144, 2822, 8288, 5644))
        assert as_tuple(roi) == FULL_SENSOR
        assert center_of(roi) == (4144, 2822)

    def test_keeps_the_requested_center_at_the_sensor_edge(self):
        roi = ImagerRoi.from_other(UnitRoi(3000, 2822, 8288, 5644))
        assert center_of(roi) == (3000, 2822)
        assert as_tuple(roi) == (0, 0, 6000, 5644)

    def test_keeps_the_fiber_center(self):
        roi = ImagerRoi.from_other(SpecRoi(fiber_x=4401, fiber_y=2407, width=7774, height=4814))
        assert center_of(roi) == (4401, 2407)
        assert (roi.x + roi.width // 2, roi.y + roi.height // 2) == (4401, 2407)


class TestBinned:
    @pytest.mark.parametrize("binning", [None, 1])
    def test_unbinned_is_the_same_roi(self, binning):
        roi = ImagerRoi(**RECT)
        assert roi.binned(binning) is roi

    def test_binning_two_halves_without_reconditioning(self):
        roi = condition((7, 1, 8272, 5640))
        binned = roi.binned(2)
        assert as_tuple(binned) == (3, 0, 4136, 2820)
        assert center_of(binned) == (2071, 1410)
