"""The citation graph recovered from stored records: edges, reports and exports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.digest import main  # noqa: E402
from scholar_crawler.graph import (  # noqa: E402
    build_graph,
    cited_target,
    citing_works_id,
    format_for,
    render_graph,
    render_network,
    to_dot,
    to_graphml,
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
    assert "1 record(s) neither cite nor are cited here" in lines[1]
    assert any("3 here" in line and "40,000 on Scholar" in line for line in lines)


def test_a_collection_without_citation_listings_says_so_instead_of_drawing_nothing() -> None:
    lines = render_network(build_graph([_record("a"), _record("b")]))
    assert lines == [
        "no citation edges in 2 records: edges come from --cites listings, "
        "which this collection has none of"
    ]


def test_graphml_is_well_formed_and_carries_the_node_attributes() -> None:
    seed = _record("seed", cited_by_url="?cites=111", year=2017, cited_by_count=41135)
    citing = _record("citing", query="cites:111", extra={"follow_depth": 1})
    document = to_graphml(build_graph([seed, citing]))
    root = ElementTree.fromstring(document)
    namespace = "{http://graphml.graphdrawing.org/xmlns}"
    nodes = root.findall(f"{namespace}graph/{namespace}node")
    edges = root.findall(f"{namespace}graph/{namespace}edge")
    assert len(nodes) == 2
    assert len(edges) == 1
    labels = {
        node.find(f'{namespace}data[@key="d0"]').text for node in nodes  # type: ignore[union-attr]
    }
    assert labels == {"Paper seed", "Paper citing"}
    assert any(node.find(f'{namespace}data[@key="d2"]') is not None for node in nodes)


def test_graphml_escapes_a_title_that_would_break_the_xml() -> None:
    hostile = _record("x", title='A <b>bold</b> & "quoted" title')
    document = to_graphml(build_graph([hostile]))
    root = ElementTree.fromstring(document)  # would raise on unescaped markup
    namespace = "{http://graphml.graphdrawing.org/xmlns}"
    label = root.find(f"{namespace}graph/{namespace}node/{namespace}data")
    assert label is not None and label.text == 'A <b>bold</b> & "quoted" title'


def test_dot_quotes_labels_and_marks_uncollected_works() -> None:
    citing = _record("citing", query="cites:999", title='Attention is all you "need"')
    document = to_dot(build_graph([citing]))
    assert document.startswith("digraph citations {")
    assert 'label="Attention is all you \\"need\\" (2020)"' in document
    assert "style=dashed" in document  # the uncollected target
    assert "n0 -> n1;" in document


def test_the_format_is_taken_from_the_suffix_and_named_formats_are_the_only_ones() -> None:
    assert format_for(".graphml") == "graphml"
    assert format_for(".DOT") == "dot"
    assert format_for(".txt") is None
    with pytest.raises(ValueError, match="unknown graph format"):
        render_graph(build_graph([]), "svg")


def test_the_digest_exports_a_graph_and_reports_the_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "records.jsonl"
    seed = _record("seed", cited_by_url="?cites=111")
    citing = _record("citing", query="cites:111")
    source.write_text("".join(json.dumps(r) + "\n" for r in (seed, citing)), encoding="utf-8")
    destination = tmp_path / "nested" / "graph.graphml"

    assert main([str(source), "--network", "--graph", str(destination)]) == 0
    printed = capsys.readouterr().out
    assert "2 records and 0 uncollected works, 1 edges" in printed
    assert f"[out] 2 nodes and 1 edges as graphml -> {destination}" in printed
    ElementTree.fromstring(destination.read_text(encoding="utf-8"))


def test_an_unknown_graph_suffix_is_refused_with_the_way_to_fix_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(_record("a")) + "\n", encoding="utf-8")
    assert main([str(source), "--graph", str(tmp_path / "graph.txt")]) == 1
    printed = capsys.readouterr().out
    assert "cannot tell the format of graph.txt" in printed
    assert "--graph-format" in printed


def test_the_graph_format_flag_overrides_the_suffix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(_record("a")) + "\n", encoding="utf-8")
    destination = tmp_path / "graph.txt"
    assert main([str(source), "--graph", str(destination), "--graph-format", "dot"]) == 0
    assert destination.read_text(encoding="utf-8").startswith("digraph citations {")
