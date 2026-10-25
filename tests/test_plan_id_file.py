"""`write_plan_id_file`: a plan's producers label each products folder with the plan's ULID."""

from __future__ import annotations

import os

import pytest

from common.filer import Filer, MoveGuardian
from common.models.plans import PLAN_ID_FILE, write_plan_id_file

ULID = "01JAD8Q4ZK7V3M2XG9T6R5N0HB"


@pytest.fixture
def moved(monkeypatch) -> list[str]:
    paths: list[str] = []
    monkeypatch.setattr(Filer, "move_ram_to_shared", lambda self, p: paths.append(p))
    yield paths
    with MoveGuardian._condition:
        MoveGuardian._products.clear()


def test_the_file_holds_the_ulid(tmp_path, moved):
    path = write_plan_id_file(tmp_path / "seq=0007", ULID)

    assert path == os.path.join(str(tmp_path / "seq=0007"), "plan_id")
    with open(path, encoding="utf-8") as f:
        assert f.read() == f"{ULID}\n"


def test_it_is_a_product_and_goes_to_the_share(tmp_path, moved):
    path = write_plan_id_file(tmp_path, ULID)

    assert os.path.realpath(path) in MoveGuardian._products, "protected, so never discarded as scratch"
    assert moved == [path]


@pytest.mark.parametrize(("folder", "ulid"), [(None, ULID), ("x", None), ("x", "")])
def test_nothing_without_a_folder_or_a_ulid(tmp_path, moved, folder, ulid):
    assert write_plan_id_file(None if folder is None else tmp_path / folder, ulid) is None
    assert moved == []
    assert not (tmp_path / "x" / PLAN_ID_FILE).exists()


def test_a_write_that_fails_does_not_raise(tmp_path, moved):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the folder should be")

    assert write_plan_id_file(blocker, ULID) is None
    assert moved == []
