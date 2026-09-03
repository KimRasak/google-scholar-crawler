"""Reading a Scholar venue string apart.

One grey line carries the venue name and, usually, the volume, issue, pages and year appended
to it. Both the grouping label and the bibliography read that line through :mod:`venues`, so the
table below is the single place the shapes Scholar actually serves are pinned.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.venues import split_venue  # noqa: E402

CASES = [
    # (raw venue, name, volume, number, pages)
    ("nature 521 (7553), 436-444, 2015", "nature", "521", "7553", "436-444"),
    (
        "Advances in neural information processing systems 27",
        "Advances in neural information processing systems",
        "27",
        None,
        None,
    ),
    ("MIT press 1 (2)", "MIT press", "1", "2", None),
    ("Journal of X 12, 3-9", "Journal of X", "12", None, "3-9"),
    ("Neurocomputing 452, 118", "Neurocomputing", "452", None, "118"),
    ("Cell 181 (2), 271–285", "Cell", "181", "2", "271–285"),
    ("Future Internet", "Future Internet", None, None, None),
    ("arXiv preprint arXiv:2105.14491", "arXiv preprint arXiv:2105.14491", None, None, None),
]


@pytest.mark.parametrize(("raw", "name", "volume", "number", "pages"), CASES)
def test_the_numeric_tail_scholar_appends_is_read_apart(
    raw: str, name: str, volume: str | None, number: str | None, pages: str | None
) -> None:
    parsed = split_venue(raw)
    assert (parsed.name, parsed.volume, parsed.number, parsed.pages) == (name, volume, number, pages)
    assert not parsed.cut


def test_a_venue_that_is_only_a_year_stays_a_name() -> None:
    # A card with no venue leaves the year alone in that position, and a volume with no name in
    # front of it would be a parse that invented a journal.
    parsed = split_venue("2021")
    assert (parsed.name, parsed.volume) == ("2021", None)


def test_a_cut_is_reported_at_the_end_it_happened() -> None:
    tail = split_venue("The world wide web …")
    assert (tail.name, tail.cut_head, tail.cut_tail) == ("The world wide web", False, True)
    both = split_venue("… on neural networks …")
    assert (both.name, both.cut_head, both.cut_tail) == ("on neural networks", True, True)
    assert both.cut is True
    assert split_venue("Future Internet").cut is False


def test_an_empty_venue_reads_as_nothing() -> None:
    for raw in ("", "   ", ",", "…"):
        parsed = split_venue(raw)
        assert parsed.name == "", raw
        assert parsed.volume is None
