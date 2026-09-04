"""The citation graph recovered from stored records: edges, reports and exports."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.graph import (  # noqa: E402
    build_graph,
    cited_target,
    citing_works_id,
    render_network,
)


def _record(key: str, *, query: str = "graph attention networks", **overrides: Any) -> dict[str, Any]:
    """Build a stored record; pass ``cited_by_url`` to give it a citing-works id."""
    record: dict[str, Any] = {
        "cluster_id": key,
        "title": f"Paper {key}",
        "link": f"https://example.org/{key}",
        "year": 2020,
        "cited_by_count": 10,
        "cited_by_url": None,
        "query": query,
    }
    record.update(overrides)
    return record


def test_a_cites_listing_says_which_work_its_records_cite() -> None:
    assert cited_target(_record("a", query="cites:12345")) == "12345"
    assert cited_target(_record("a", query="graph attention networks")) is None
    assert cited_target(_record("a", query="cluster:999")) is None
    assert cited_target(_record("a", query="cites:")) is None
    assert cited_target({}) is None


def test_a_works_own_citing_listing_id_comes_from_its_cited_by_url() -> None:
    assert citing_works_id(_record("a", cited_by_url="?cites=555")) == "555"
    assert citing_works_id(_record("a")) is None
    assert citing_works_id({"cited_by_url": "https://example.org/nothing"}) is None


def test_edges_point_from_the_citing_record_to_the_cited_work() -> None:
    seed = _record("seed", cited_by_url="https://scholar.google.com/scholar?cites=111")
    citing = _record("citing", query="cites:111")
    graph = build_graph([seed, citing])
    assert graph.edges == [("citing", "seed")]
    assert graph.stubs == set()
    assert graph.in_degree() == {"seed": 1}
    assert graph.out_degree() == {"citing": 1}


def test_a_cited_work_that_was_never_collected_becomes_a_marked_stub() -> None:
    graph = build_graph([_record("citing", query="cites:999")])
    assert graph.edges == [("citing", "cites:999")]
    assert graph.stubs == {"cites:999"}
    assert graph.nodes["cites:999"].stub is True
    assert graph.nodes["cites:999"].label == "uncollected work 999"


def test_every_edge_of_a_repeated_record_survives_the_merge() -> None:
    # One work can appear under several --cites listings; merging keeps one query value only.
    first = _record("both", query="cites:111")
    again = _record("both", query="cites:222")
    seeds = [
        _record("one", cited_by_url="https://scholar.google.com/scholar?cites=111"),
        _record("two", cited_by_url="https://scholar.google.com/scholar?cites=222"),
    ]
    merged = [*seeds, first]  # what the digest would keep
    graph = build_graph([*seeds, first, again], merged)
    assert sorted(graph.edges) == [("both", "one"), ("both", "two")]


def test_an_edge_is_listed_once_however_often_it_is_observed() -> None:
    seed = _record("seed", cited_by_url="https://scholar.google.com/scholar?cites=111")
    citing = _record("citing", query="cites:111")
    graph = build_graph([seed, citing, dict(citing)])
    assert graph.edges == [("citing", "seed")]


def test_a_work_listed_among_its_own_citing_works_gets_no_self_edge() -> None:
    itself = _record("self", query="cites:selfid", cited_by_url="?cites=selfid")
    assert build_graph([itself]).edges == []


def test_a_record_filtered_out_of_the_drawn_set_contributes_no_edge() -> None:
    seed = _record("seed", cited_by_url="https://scholar.google.com/scholar?cites=111")
    dropped = _record("dropped", query="cites:111")
    graph = build_graph([seed, dropped], [seed])
    assert graph.edges == []
    assert set(graph.nodes) == {"seed"}


def test_components_ignore_direction_and_come_largest_first() -> None:
    left = _record("left", cited_by_url="?cites=1")
    middle = _record("middle", query="cites:1", cited_by_url="?cites=2")
    right = _record("right", query="cites:2")
    lonely = _record("lonely", cited_by_url="?cites=9")
    graph = build_graph([left, middle, right, lonely])
    components = graph.components()
    assert [len(group) for group in components] == [3, 1]
    assert set(components[0]) == {"left", "middle", "right"}


def test_the_network_report_names_the_hubs_and_the_records_standing_alone() -> None:
    seed = _record("seed", cited_by_url="?cites=111", cited_by_count=40000)
    citing = [_record(f"c{index}", query="cites:111") for index in range(3)]
    alone = _record("alone")
    lines = render_network(build_graph([seed, *citing, alone]))
    assert lines[0].startswith("5 records and 0 uncollected works, 3 edges")
    assert "1 record with no edge either way" in lines[1]
    assert any("3 here" in line and "40,000 on Scholar" in line for line in lines)


def test_asking_for_no_entries_drops_the_heading_too() -> None:
    seed = _record("seed", cited_by_url="?cites=111", cited_by_count=40000)
    citing = [_record(f"c{index}", query="cites:111") for index in range(3)]
    lines = render_network(build_graph([seed, *citing]), top=0)
    assert len(lines) == 2, "a list of nothing needs no heading"
    assert not any("most cited" in line for line in lines)


def test_a_collection_without_citation_listings_says_so_instead_of_drawing_nothing() -> None:
    lines = render_network(build_graph([_record("a"), _record("b")]))
    assert lines == [
        "no citation edges in 2 records: edges come from --cites listings, "
        "which this collection has none of"
    ]
