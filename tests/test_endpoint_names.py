"""Endpoint names are one symbol per service, not a literal per call site (MAST_unit#35).

Both sides of the wire used to name these independently -- the unit built paths by
concatenation, the shared plan client re-typed the same strings -- so a rename showed up only
as a 404 at run time. These enums are the single source; the tests below pin the two properties
that make them worth having.
"""

from __future__ import annotations

import inspect

from common.const import SpecEndpoint, UnitEndpoint


def test_the_names_are_usable_wherever_a_string_is():
    """`StrEnum`, so a client can pass a member to any API that wants a path fragment."""
    assert UnitEndpoint.STATUS == "status"
    assert f"/unit/{UnitEndpoint.ABORT}" == "/unit/abort"
    assert SpecEndpoint.ACQUIRE.upper() == "ACQUIRE"


def test_the_unit_contract_set_is_exactly_the_contract_tier():
    """Operator and diagnostic verbs stay literal: listing one here implies a promise the
    contract does not make, and the component lifecycle verbs are single-sourced on the
    `Component` ABC instead (MAST_unit#40)."""
    assert {e.value for e in UnitEndpoint} == {
        "execute_assignment",
        "status",
        "abort",
        "startup",
        "shutdown",
    }


def test_the_spectrograph_has_no_execute_assignment():
    """The absence is the point, and it is load-bearing.

    The shared plan client calls the spectrograph with `execute_assignment` in three places
    and MAST_spec serves no such route -- checked against all 52 of its registrations. Adding a
    member here would turn a live mismatch into a documented contract, so those call sites stay
    literal until the mismatch is resolved.
    """
    assert not hasattr(SpecEndpoint, "EXECUTE_ASSIGNMENT")
    assert "execute_assignment" not in {e.value for e in SpecEndpoint}


def test_each_service_has_its_own_enum():
    """A sibling per service rather than one shared set: the two hosts do not serve the same
    routes, and a single enum would let a caller name one the target does not have."""
    assert UnitEndpoint is not SpecEndpoint
    assert {e.value for e in SpecEndpoint} - {e.value for e in UnitEndpoint} == {"powerdown", "acquire"}


def test_they_live_next_to_the_base_paths_they_complete():
    """A path is `BASE_<SERVICE>_PATH` + a name from that service's enum; keeping them in one
    module is what makes that pairing findable."""
    from common import const

    assert inspect.getmodule(UnitEndpoint) is const
    assert inspect.getmodule(SpecEndpoint) is const
    assert hasattr(const.Const, "BASE_UNIT_PATH")
    assert hasattr(const.Const, "BASE_SPEC_PATH")
