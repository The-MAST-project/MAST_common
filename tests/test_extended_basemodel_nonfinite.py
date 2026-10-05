"""NaN and infinity must survive serialisation in BOTH json modes.

`ser_json_inf_nan="strings"` applies only to `model_dump_json()`. `model_dump(mode="json")`
ignores it and returns a raw float `nan`, which is what the pydantic-v1 `json_encoders`
hook this class replaced used to cover -- it ran at field level, so both modes went through
it. The replacement was assumed to be equivalent and is not.

That matters because a "json-mode" dict is by definition one that is safe to hand to
`json.dumps`, and a bare `NaN` token is invalid JSON. Python's own `json` module emits it
happily and reads it back, so the break is invisible from Python and only shows up at a
non-Python consumer -- the GUI.

Nothing on master exercised this, which is why the regression went unnoticed; it was caught
by MAST_unit's calibration branch, whose status projection dumps with `mode="json"`.
"""

import json
import math

import pytest
from pydantic import Field

from common.extended_basemodel import ExtendedBaseModel


class Inner(ExtendedBaseModel):
    v: float | None = None


class Sample(ExtendedBaseModel):
    scalar: float | None = None
    finite: float = 1.5
    items: list[float] = Field(default_factory=list)
    mapping: dict[str, float] = Field(default_factory=dict)
    nested: Inner | None = None
    text: str = "hi"
    flag: bool = True
    count: int = 3


@pytest.mark.parametrize(
    ("value", "spelling"),
    [(float("nan"), "NaN"), (float("inf"), "Infinity"), (float("-inf"), "-Infinity")],
)
def test_both_json_modes_spell_nonfinite_floats(value, spelling):
    model = Sample(scalar=value)

    assert model.model_dump(mode="json")["scalar"] == spelling
    assert json.loads(model.model_dump_json())["scalar"] == spelling


def test_nonfinite_inside_containers_and_nested_models():
    model = Sample(
        items=[1.0, float("inf")],
        mapping={"a": float("nan"), "b": 2.0},
        nested=Inner(v=float("nan")),
    )

    dumped = model.model_dump(mode="json")

    assert dumped["items"] == [1.0, "Infinity"]
    assert dumped["mapping"] == {"a": "NaN", "b": 2.0}
    assert dumped["nested"] == {"v": "NaN"}


def test_a_json_mode_dump_is_valid_json():
    """The whole point of mode='json': the result must be safe for `json.dumps`.

    `json.dumps` would otherwise emit a bare `NaN`, which Python reads back happily and
    every other JSON parser rejects -- so this is asserted against a strict decoder rather
    than by round-tripping through Python.
    """
    dumped = Sample(scalar=float("nan"), items=[float("inf")]).model_dump(mode="json")

    def reject(token):
        raise AssertionError(f"bare {token} token in the payload")

    json.loads(json.dumps(dumped), parse_constant=reject)


def test_finite_and_non_float_values_are_untouched():
    """The serialiser sees every field, so it has to be a no-op for almost all of them."""
    dumped = Sample(scalar=2.5).model_dump(mode="json")

    assert dumped == {
        "scalar": 2.5,
        "finite": 1.5,
        "items": [],
        "mapping": {},
        "nested": None,
        "text": "hi",
        "flag": True,
        "count": 3,
    }


def test_python_mode_still_returns_real_floats():
    """Only the JSON modes stringify; a python-mode dump stays numeric."""
    dumped = Sample(scalar=float("nan")).model_dump()

    assert isinstance(dumped["scalar"], float)
    assert math.isnan(dumped["scalar"])


@pytest.mark.parametrize("spelling", ["NaN", "Infinity", "-Infinity"])
def test_the_spellings_rehydrate(spelling):
    """Round trip closes: pydantic coerces these back, so no custom decoder is needed.

    Compared via `isnan` rather than `==` for the NaN case -- NaN is the one float that is
    not equal to itself, so an equality assertion here passes for infinity and fails for
    NaN no matter what the code does.
    """
    rehydrated = Sample(scalar=spelling).scalar
    expected = float(spelling)

    if math.isnan(expected):
        assert math.isnan(rehydrated)
    else:
        assert rehydrated == expected
