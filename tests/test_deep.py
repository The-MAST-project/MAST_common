"""Tests for `common.deep.deep_dict_update` (issue #37).

The regression: the recursion guard tested key *presence* (`key in original`)
rather than the existing value's *type*, so a dict update landing on a non-dict
original recursed into that non-dict and raised

    TypeError: 'NoneType' object does not support item assignment

which took `Config.get_unit()` down for any unit whose config supplied a dict
where the `common` unit document held `None`.
"""

import pytest

from common.deep import deep_dict_update


@pytest.mark.parametrize(
    "original_value",
    [None, 5, "text", [1, 2], True],
    ids=["none", "int", "str", "list", "bool"],
)
def test_dict_update_replaces_non_dict_original(original_value):
    """A dict update over a non-dict original replaces it -- and does not raise."""
    original = {"a": original_value}
    deep_dict_update(original, {"a": {"b": 1}})
    assert original == {"a": {"b": 1}}


def test_dict_update_adds_absent_key():
    original = {}
    deep_dict_update(original, {"a": {"b": 1}})
    assert original == {"a": {"b": 1}}


def test_nested_dicts_are_merged_not_replaced():
    """Where both sides are dicts, keys the update omits must survive."""
    original = {"a": {"keep": 1, "override": 2}}
    deep_dict_update(original, {"a": {"override": 3, "added": 4}})
    assert original == {"a": {"keep": 1, "override": 3, "added": 4}}


def test_unrelated_keys_survive():
    original = {"untouched": {"x": 1}, "a": None}
    deep_dict_update(original, {"a": {"b": 1}})
    assert original == {"untouched": {"x": 1}, "a": {"b": 1}}


def test_non_dict_update_replaces_dict_original():
    """The converse direction: a scalar update flattens a dict original."""
    original = {"a": {"b": 1}}
    deep_dict_update(original, {"a": None})
    assert original == {"a": None}


def test_update_is_in_place_and_returns_none():
    original = {"a": 1}
    assert deep_dict_update(original, {"b": 2}) is None
    assert original == {"a": 1, "b": 2}


def test_get_unit_merge_shape_from_issue_37():
    """The real shape that broke `MAST_control`: `calibration.products.focuser`
    is `None` in the `common` unit document and a dict in `mast02`'s."""
    common_unit = {
        "name": "common",
        "calibration": {"products": {"focuser": None}},
    }
    mast02 = {
        "name": "mast02",
        "calibration": {"products": {"focuser": {"best_position": 12050, "n_samples": 7}}},
    }

    deep_dict_update(common_unit, mast02)

    assert common_unit["calibration"]["products"]["focuser"] == {
        "best_position": 12050,
        "n_samples": 7,
    }
    assert common_unit["name"] == "mast02"
