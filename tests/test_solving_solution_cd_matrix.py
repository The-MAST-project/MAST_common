"""Regression test: the solver path's ``SolvingSolution`` carries the WCS CD matrix.

There are two ``SolvingSolution`` classes in this repo -- this one, and the older
copy in ``common/solving.py`` that predates the 2025-07-02 move into
``common.interfaces`` and survives only because ``SolverId`` still lives beside it.
The solver backends import *this* one, so a field added to the other is a field the
solvers do not have.

That distinction is a crash rather than a silent miss: ``SolvingSolution`` is a
pydantic ``BaseModel``, and assigning an undeclared attribute raises
``ValueError: "SolvingSolution" object has no field "cd1_1"``. The write in
``MAST_unit`` ``solvers/mastrometry.py`` happens on the success path, right after the
CRVAL override, so the failure lands on every solve that WORKS.

The consumer side reads the matrix out of a serialized dict (``solution.get("cd1_1")``),
where a missing key is just ``None`` -- which is why the arithmetic can be covered in
full and the field still be absent. Hence a test on the model itself.
"""

from __future__ import annotations

import pytest

pytest.importorskip("common.interfaces.solving", reason="solving import chain unavailable")
from common.interfaces.solving import SolvingSolution  # noqa: E402

# A plausible solved frame: ~0.524"/px, near-90-degree rotation, mirrored parity
# (cd1_1 and cd2_2 of opposite sign), degrees per downsampled pixel.
CD = {"cd1_1": 1.4557e-4, "cd1_2": -2.0e-7, "cd2_1": -2.1e-7, "cd2_2": -1.4557e-4}


def test_the_matrix_can_be_assigned_after_construction():
    """The producer's pattern: build the solution, then fill it from the FITS header."""
    solution = SolvingSolution()
    for term, value in CD.items():
        setattr(solution, term, value)

    assert {term: getattr(solution, term) for term in CD} == CD


def test_the_matrix_survives_serialization():
    """The consumer reads a dict, so an unserialized field is as good as an absent one."""
    dumped = SolvingSolution(**CD).model_dump()

    assert {term: dumped[term] for term in CD} == CD


def test_an_unsolved_solution_has_no_matrix_rather_than_zeros():
    """A default of 0.0 would be a valid-looking degenerate matrix; None cannot be mistaken."""
    assert all(getattr(SolvingSolution(), term) is None for term in CD)
