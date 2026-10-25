"""`write_plan_id_file` / `write_batch_id_file`: a producer labels each products folder with the
ULID of the plan, or the batch, that drove it."""

from __future__ import annotations

import os

import pytest

from common.filer import Filer, MoveGuardian
from common.models.plans import BATCH_ID_FILE, PLAN_ID_FILE, write_batch_id_file, write_plan_id_file

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


def test_a_batch_gets_its_own_file(tmp_path, moved):
    batch = "01JAD8Q4ZK7V3M2XG9T6R5BTCH"
    path = write_batch_id_file(tmp_path, batch)

    assert os.path.basename(path) == BATCH_ID_FILE == "batch_id"
    with open(path, encoding="utf-8") as f:
        assert f.read() == f"{batch}\n"
    assert moved == [path]
    assert not (tmp_path / PLAN_ID_FILE).exists()


def test_a_folder_can_carry_both(tmp_path, moved):
    write_plan_id_file(tmp_path, ULID)
    write_batch_id_file(tmp_path, "01JAD8Q4ZK7V3M2XG9T6R5BTCH")
    assert sorted(os.listdir(tmp_path)) == ["batch_id", "plan_id"]


def test_a_ulid_object_is_written_as_its_text(tmp_path, moved):
    """`Batch.ulid` is a `ULID`, not a str."""
    from ulid import ULID

    batch = ULID()
    path = write_batch_id_file(tmp_path, batch)
    with open(path, encoding="utf-8") as f:
        assert f.read() == f"{batch}\n"
