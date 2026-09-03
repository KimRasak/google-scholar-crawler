"""Overview counts and per-dimension grouping over collected records."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.analysis import (  # noqa: E402
    first_author,
    group_label,
    group_records,
    normalize_venue,
    render_groups,
    render_summary,
    summarize,
)


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "cluster_id": "c1",
        "title": "A paper",
        "link": "https://example.org/a",
        "year": 2020,
        "cited_by_count": 10,
        "venue": "A Journal",
        "citation_only": False,
        "extra": {"follow_depth": 0},
    }
    record.update(overrides)
    return record


def test_summary_counts_and_ranking() -> None:
    records = [
        _record(cluster_id="a", year=2021, cited_by_count=7, venue="J1"),
        _record(cluster_id="b", year=2021, cited_by_count=70, venue="J1", extra={"follow_depth": 1}),
        _record(cluster_id="c", year=None, cited_by_count=None, venue=None, citation_only=True, extra={}),
        _record(cluster_id="d", year=2019, cited_by_count=1, extra={"bibtex_key": "x2019y"}),
    ]
    summary = summarize(records, top=2)
    assert (summary.records, summary.citations) == (4, 78)
    assert (summary.with_bibtex, summary.citation_only, summary.unknown_year) == (1, 1, 1)
    assert summary.years == [(2021, 2), (2019, 1)]
    assert summary.venues[0] == ("J1", 2)
    assert [entry[0] for entry in summary.top] == [70, 7]
    assert summary.levels == [(0, 3), (1, 1)]


def test_rendered_summary_lists_levels_only_when_they_differ() -> None:
    flat = render_summary(summarize([_record()]))
    assert not any(line.startswith("graph levels") for line in flat)
    mixed = render_summary(summarize([_record(), _record(cluster_id="c2", extra={"follow_depth": 1})]))
    assert any(line.startswith("graph levels") for line in mixed)
    assert any("most cited" in line for line in mixed)


def test_the_first_author_comes_from_either_field() -> None:
    assert first_author(_record(authors="P Veličković, G Cucurull, A Casanova…")) == "P Veličković"
    assert first_author(_record(authors=None, byline="S Brody, U Alon - arXiv, 2021 - arxiv.org")) == (
        "S Brody"
    )
    assert first_author(_record(authors="A Casanova…")) == "A Casanova"
    assert first_author(_record(authors=None, byline="")) is None


def test_venue_spellings_collapse_to_one_group_label() -> None:
    assert normalize_venue("arXiv preprint arXiv:2105.14491") == "arXiv preprint"
    assert normalize_venue("arXiv preprint arXiv …") == "arXiv preprint"
    assert normalize_venue("nature 521 (7553), 436-444, 2015") == "nature"
    assert normalize_venue("Advances in neural information processing systems 27") == (
        "Advances in neural information processing systems"
    )
    assert normalize_venue("Future Internet") == "Future Internet"
    assert normalize_venue("2021") == "2021"  # nothing left to trim, so the value survives


def test_group_labels_per_dimension() -> None:
    record = _record(authors="Y Bengio, I Goodfellow", venue="nature 521 (7553), 2015", year=2015)
    assert group_label(record, "author") == "Y Bengio"
    assert group_label(record, "venue") == "nature"
    assert group_label(record, "year") == "2015"
    assert group_label(record, "level") == "L0"
    assert group_label(_record(venue="J", citation_only=True), "venue") is None
    assert group_label(_record(year=None), "year") is None
    with pytest.raises(ValueError, match="unknown group key"):
        group_label(record, "publisher")


def test_groups_rank_by_citations_and_report_medians() -> None:
    records = [
        _record(cluster_id="a", venue="Nature", cited_by_count=100, year=2010),
        _record(cluster_id="b", venue="nature", cited_by_count=300, year=2020),
        _record(cluster_id="c", venue="Nature", cited_by_count=200, year=2015, title="Middle"),
        _record(cluster_id="d", venue="Future Internet", cited_by_count=350, year=2024),
    ]
    groups = group_records(records, "venue")
    assert [group.label for group in groups] == ["Nature", "Future Internet"]
    top = groups[0]
    assert (top.records, top.citations, top.median_citations) == (3, 600, 200)
    assert (top.first_year, top.last_year) == (2010, 2020)
    assert top.best == (300, "A paper")


def test_rendered_groups_align_and_report_a_remainder() -> None:
    records = [_record(cluster_id=f"c{index}", venue=f"V{index}") for index in range(4)]
    lines = render_groups(group_records(records, "venue"), "venue", limit=2)
    assert lines[0].strip().startswith("by venue")
    assert "count" in lines[0] and "median" in lines[0]
    assert len(lines) == 4
    assert lines[-1].strip() == "... and 2 more groups"
    # Each listed group holds one record, right-aligned under the header's count column.
    count_end = lines[0].index("count") + len("count")
    assert [line[:count_end].rstrip()[-1] for line in lines[1:3]] == ["1", "1"]
    assert render_groups([], "author") == ["by author: nothing to group"]
