"""A guiding session's folder: one per session, named as an acquisition's is, numbered per night."""

from __future__ import annotations

import re
from pathlib import Path

from common.paths import PathMaker

NAME = re.compile(r"seq=(\d{4}),time=\d{2}-\d{2}-\d{2}_\d{3}")


def test_it_is_a_folder_under_guidings_named_like_an_acquisition():
    folder = PathMaker().make_guiding_session_folder()

    path = Path(folder)
    assert path.is_dir() and path.parent.name == "Guidings"
    assert NAME.fullmatch(path.name), path.name
    assert path.parent.parent.name == PathMaker().make_observing_night_folder().rsplit("/", 1)[-1]


def test_each_session_gets_the_next_number():
    pm = PathMaker()

    first, second = pm.make_guiding_session_folder(), pm.make_guiding_session_folder()

    numbers = [int(NAME.match(Path(f).name).group(1)) for f in (first, second)]  # type: ignore[union-attr]
    assert numbers[1] == numbers[0] + 1 and first != second


def test_tags_are_appended_and_an_empty_tag_is_named():
    folder = PathMaker().make_guiding_session_folder(tags={"target": "3.5,20.1", "dry": None})

    assert Path(folder).name.endswith(",target=3.5,20.1,dry")


def test_the_session_numbering_is_not_the_acquisitions():
    pm = PathMaker()
    before = int(NAME.match(Path(pm.make_guiding_session_folder()).name).group(1))  # type: ignore[union-attr]
    for _ in range(3):
        pm.make_acquisition_folder()

    after = int(NAME.match(Path(pm.make_guiding_session_folder()).name).group(1))  # type: ignore[union-attr]

    # Two counters, kept in two folders: three acquisitions in between do not move the sessions' numbering.
    assert after == before + 1
    assert (Path(pm.make_guidings_folder()) / "seq.txt").read_text() == str(after)
