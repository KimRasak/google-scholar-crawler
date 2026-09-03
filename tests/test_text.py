"""A cut line must look cut, in every report that prints one."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.analysis import Group, render_groups  # noqa: E402
from scholar_crawler.refresh import Aged  # noqa: E402
from scholar_crawler.text import clip  # noqa: E402

LONG = "Knowledge graph embedding: A survey from the perspective of representation learning"


def test_text_that_fits_is_returned_unchanged() -> None:
    assert clip("short", 10) == "short"
    assert clip("exactly-10", 10) == "exactly-10"


def test_a_cut_is_marked_and_stays_within_the_column() -> None:
    cut = clip(LONG, 40)
    assert len(cut) == 40
    assert cut.endswith("…")
    assert LONG.startswith(cut[:-1])


def test_a_cut_does_not_leave_a_dangling_space() -> None:
    assert clip("one two three", 9) == "one two…"  # the space before the mark is dropped


def test_a_width_with_no_room_for_text_is_refused() -> None:
    with pytest.raises(ValueError, match="leaves no room"):
        clip(LONG, 1)


def test_the_refresh_list_marks_a_title_it_shortened() -> None:
    record = {"title": LONG, "cited_by_count": 248}
    line = Aged(record=record, age_days=400, pressure=1.0, target="123").describe()
    assert "…" in line, "a title cut mid-word reads as damaged data unless the cut is marked"
    assert LONG not in line


def test_a_group_table_marks_a_representative_title_it_shortened() -> None:
    group = Group(
        label="arXiv preprint",
        records=2,
        citations=10,
        median_citations=5,
        first_year=2020,
        last_year=2021,
        best=(10, LONG),
    )
    assert any("…" in line for line in render_groups([group], "venue"))
