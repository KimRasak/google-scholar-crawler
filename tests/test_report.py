"""The Markdown report: what it states, and what it refuses to overstate."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.digest import main  # noqa: E402
from scholar_crawler.report import build_report  # noqa: E402


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "title": "Graph attention networks",
        "link": "https://arxiv.org/abs/1710.10903",
        "authors": "P Veličković, G Cucurull",
        "venue": "arXiv preprint",
        "year": 2017,
        "cited_by_count": 41135,
        "cited_by_url": "https://scholar.google.com/scholar?cites=1",
        "cluster_id": "uQm0ZqKg100J",
        "query": "graph attention networks",
    }
    record.update(overrides)
    return record


CORPUS = [
    _record(),
    _record(
        cluster_id="deep2015",
        title="Deep learning",
        authors="Y LeCun, Y Bengio",
        venue="Nature",
        year=2015,
        cited_by_count=118913,
        link=None,
    ),
    _record(
        cluster_id="how2021",
        title="How attentive are graph attention networks?",
        authors="S Brody, U Alon",
        venue="arXiv preprint",
        year=2021,
        cited_by_count=3429,
    ),
    _record(
        cluster_id="kgat2019",
        title="Kgat: knowledge graph attention network",
        authors="X Wang, X He",
        venue="Proceedings of the 25th ACM",
        year=2019,
        cited_by_count=3205,
        query="knowledge graph",
    ),
]


def _section(markdown: str, heading: str) -> str:
    body = markdown.split(f"## {heading}", 1)
    assert len(body) == 2, f"missing section: {heading}"
    return body[1].split("\n## ", 1)[0]


def test_the_report_states_where_the_numbers_came_from() -> None:
    markdown = build_report(CORPUS, title="A first pass")
    assert markdown.startswith("# A first pass\n")
    assert "Built from 4 records" in markdown
    assert "nothing was re-fetched" in markdown  # the reader must not think this is fresh data
    assert markdown.endswith("\n")


def test_at_a_glance_counts_records_venues_and_authors() -> None:
    glance = _section(build_report(CORPUS), "At a glance")
    assert "**4 records**, 166,682 citations in total" in glance
    assert "published **2015–2021**" in glance
    assert "**3 venues**, **4 first authors**" in glance


def test_the_most_cited_table_links_titles_that_have_a_destination() -> None:
    table = _section(build_report(CORPUS, top=3), "Most cited works (top 3)")
    rows = [line for line in table.splitlines() if line.startswith("| ")]
    assert len(rows) == 5  # header, separator, three works
    assert "118,913" in rows[2] and "Deep learning" in rows[2]
    assert "[Deep learning](" not in rows[2]  # this record carries no link
    assert "[Graph attention networks](<https://arxiv.org/abs/1710.10903>)" in rows[3]


def test_groups_are_tabulated_by_venue_and_by_first_author() -> None:
    markdown = build_report(CORPUS)
    venues = _section(markdown, "Where this work is published")
    assert "| Nature | 1 | 118,913 |" in venues
    assert "| arXiv preprint | 2 |" in venues
    authors = _section(markdown, "Who wrote it")
    assert "| P Veličković | 1 |" in authors
    assert "| S Brody | 1 |" in authors


def test_the_year_chart_scales_against_the_busiest_year() -> None:
    records = [_record(cluster_id=f"c{index}", year=2019) for index in range(4)]
    records.append(_record(cluster_id="c9", year=2020))
    chart = _section(build_report(records), "When it was published")
    lines = [line for line in chart.splitlines() if line.startswith("20")]
    assert lines[0].startswith("2019  ") and lines[0].endswith(" 4")
    assert lines[1].startswith("2020  ") and lines[1].endswith(" 1")
    assert lines[0].count("▇") > lines[1].count("▇")


def test_the_report_says_what_was_searched() -> None:
    searched = _section(build_report(CORPUS), "What was searched")
    assert "| graph attention networks | 3 |" in searched
    assert "| knowledge graph | 1 |" in searched


def test_the_report_carries_its_own_data_quality_section() -> None:
    clean = _section(build_report(CORPUS), "How much of this to trust")
    assert "Every field parsed plausibly" in clean

    doubtful = build_report([*CORPUS, _record(cluster_id="odd", venue="521 (7553), 436-444")])
    section = _section(doubtful, "How much of this to trust")
    assert "venue looks like pages" in section
    assert "error" in section


def test_table_cells_survive_a_title_containing_a_pipe() -> None:
    markdown = build_report([_record(title="Attention | is all you need")])
    assert "Attention \\| is all you need" in markdown
    assert "| Attention | is" not in markdown  # an unescaped pipe would split the row


def test_a_missing_field_is_an_em_dash_not_an_empty_cell() -> None:
    markdown = build_report([_record(year=None, venue=None)])
    row = next(line for line in markdown.splitlines() if "Graph attention networks](" in line)
    assert row.count("| — ") == 2


def test_the_digest_writes_the_report_and_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "records.jsonl"
    with source.open("w", encoding="utf-8") as handle:
        for record in CORPUS:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    destination = tmp_path / "nested" / "report.md"

    assert main([str(source), "--report", str(destination), "--report-title", "Overview", "--quiet"]) == 0
    printed = capsys.readouterr().out
    assert f"[out] report on 4 records -> {destination}" in printed
    assert destination.read_text(encoding="utf-8").startswith("# Overview\n")


def test_a_report_is_output_enough_for_quiet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(CORPUS[0]) + "\n", encoding="utf-8")
    assert main([str(source), "--quiet"]) == 1  # nothing to write at all
    assert "--quiet needs --out" in capsys.readouterr().out


def test_a_title_made_of_markdown_punctuation_still_reads_as_a_title() -> None:
    # Real titles carry these: *SEM 2021, C*-algebras, [Re] reproducibility, word2vec_extended.
    # Unescaped, a reader turns them into emphasis, code spans or links, and the report then
    # shows a title nobody collected.
    hostile = _record(
        title="*SEM: [Re] C*-algebras & word2vec_extended `code` <tag> | pipe",
        link="https://example.org/paper?q=(open",
        cited_by_count=9,
    )
    row = next(
        line
        for line in _section(build_report([hostile], top=1), "Most cited works (top 1)").splitlines()
        if line.startswith("| 9 ")
    )
    assert r"\*SEM: \[Re\] C\*-algebras & word2vec\_extended \`code\` \<tag\> \| pipe" in row
    assert "(<https://example.org/paper?q=(open>)" in row, "a bare paren would end the link early"


def test_one_of_something_is_not_written_as_plural() -> None:
    glance = _section(build_report([_record(venue="Nature", authors="A Author")]), "At a glance")
    assert "**1 record**" in glance
    assert "**1 venue**, **1 first author**" in glance
