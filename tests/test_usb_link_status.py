"""The guide camera's USB link and the unit's caveats are reported in status (MAST_unit#264).

A camera on a USB 2.0 path works, just ~6.5x slower to read out, so nothing in status
distinguished it from a correctly wired unit. `usb_link` names the link on the PHD2 imager
backend; `caveats` carries degradations that are not faults, kept apart from `errors`.
"""

from __future__ import annotations

import pytest

statuses = pytest.importorskip("common.models.statuses", reason="models package import chain unavailable")
from common.models.statuses import FullUnitStatus, PHD2ImagerStatus, UsbLink  # noqa: E402


def test_usb_link_defaults_to_unknown():
    assert PHD2ImagerStatus().usb_link is UsbLink.Unknown


@pytest.mark.parametrize("link", list(UsbLink))
def test_usb_link_round_trips_as_its_name(link: UsbLink):
    dumped = PHD2ImagerStatus(usb_link=link).model_dump(mode="json")
    assert dumped["usb_link"] == link.value
    assert PHD2ImagerStatus.model_validate(dumped).usb_link is link


def test_usb_link_values_are_the_link_names():
    assert {link.value for link in UsbLink} == {"SuperSpeed", "HighSpeed", "unknown"}


def test_caveats_absent_by_default():
    assert FullUnitStatus(id=1).caveats is None


def test_caveats_are_separate_from_errors():
    status = FullUnitStatus(id=1, caveats=["imager: guide camera on a USB 2.0 path"])
    assert status.caveats == ["imager: guide camera on a USB 2.0 path"]
    assert status.errors is None


def test_a_component_has_no_caveats_unless_it_says_so():
    """Concrete, not abstract: only a component with something to report overrides it."""
    from common.interfaces.components import Component

    assert "caveats" not in Component.__abstractmethods__
    assert Component.caveats.fget(None) == []
