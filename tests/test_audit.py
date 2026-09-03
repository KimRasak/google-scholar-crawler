"""The record audit: what it flags, what it leaves alone, and what it reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.audit import CHECKS, AuditTally, audit_records, render_audit  # noqa: E402
from scholar_crawler.digest import main  # noqa: E402
from scholar_crawler.parser import parse_author_page, parse_result_page  # noqa: E402
from tests.fixtures import AUTHOR_PAGE_HTML, RESULT_PAGE_HTML  # noqa: E402


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "title": "Graph attention networks",
        "cluster_id": "uQm0ZqKg100J",
        "byline": "P Veličković, G Cucurull - arXiv preprint, 2017 - arxiv.org",
        "authors": "P Veličković, G Cucurull",
        "venue": "arXiv preprint",
        "year": 2017,
        "cited_by_count": 22000,
        "cited_by_url": "https://scholar.google.com/scholar?cites=1",
        "versions_count": 5,
        "query": "graph attention",
    }
    record.update(overrides)
    return record


def _flagged(record: dict[str, Any]) -> set[str]:
    return {finding.check.name for finding in audit_records([record])}


def test_a_well_parsed_record_trips_nothing() -> None:
    assert _flagged(_record()) == set()


def test_a_venue_that_is_really_a_page_range_is_an_error() -> None:
    assert "venue_looks_like_pages" in _flagged(_record(venue="521 (7553), 436-444"))
    assert "venue_looks_like_pages" in _flagged(_record(venue="12(3)"))
    assert "venue_looks_like_pages" not in _flagged(_record(venue="Nature 521"))


def test_a_year_left_inside_the_venue_is_an_error() -> None:
    # This is what splits one journal into one group per year.
    assert "venue_keeps_year" in _flagged(_record(venue="Advances in NeurIPS 27, 2014"))


def test_a_year_the_byline_never_mentioned_is_an_error() -> None:
    assert "year_disagrees_with_byline" in _flagged(_record(year=1998))
    assert "year_out_of_range" in _flagged(_record(year=436, byline="A Author - Nature, 436"))
    assert "year_out_of_range" not in _flagged(_record(year=None, byline="A Author - Nature"))
    # A byline carrying no year at all cannot disagree with the stored one.
    assert "year_disagrees_with_byline" not in _flagged(_record(byline="A Author - Nature"))


def test_a_citation_count_with_no_link_is_an_error() -> None:
    assert "citations_without_link" in _flagged(_record(cited_by_url=None))
    assert "citations_without_link" not in _flagged(_record(cited_by_count=0, cited_by_url=None))
    assert "negative_count" in _flagged(_record(versions_count=-1))


def test_missing_and_lossy_fields_are_warnings_not_errors() -> None:
    severities = {
        finding.check.name: finding.check.severity
        for finding in audit_records(
            [
                _record(venue=None, year=None, authors=None, cluster_id=None),
                _record(authors="A Author, B Author…"),
                _record(venue="nature.com"),
                _record(venue="… on neural networks …"),
                _record(title="[PDF] Graph attention networks"),
            ]
        )
    }
    assert severities["venue_missing"] == "warn"
    assert severities["year_missing"] == "warn"
    assert severities["authors_missing"] == "warn"
    assert severities["cluster_id_missing"] == "warn"
    assert severities["authors_truncated"] == "warn"
    assert severities["venue_is_hostname"] == "warn"
    assert severities["venue_truncated"] == "warn"
    assert severities["title_tagged"] == "warn"


def test_a_venue_scholar_elided_is_flagged_at_either_end() -> None:
    # Scholar elides long venues from both sides on a result page, e.g.
    # "… IEEE transactions on neural networks …", and a bibliography would cite it verbatim.
    assert "venue_truncated" in _flagged(_record(venue="… on neural networks …"))
    assert "venue_truncated" in _flagged(_record(venue="ACM Transactions on …"))
    assert "venue_truncated" not in _flagged(_record(venue="Journal of Big Data"))


def test_a_citation_only_record_is_not_blamed_for_having_no_card_id() -> None:
    assert "cluster_id_missing" not in _flagged(_record(cluster_id=None, citation_only=True))
    assert "cluster_id_missing" in _flagged(_record(cluster_id=None))


def test_findings_carry_counts_shares_and_examples() -> None:
    records = [_record(), _record(venue=None), _record(venue=None), _record(year=1066)]
    findings = audit_records(records)
    names = [finding.check.name for finding in findings]
    assert names[0] in {"year_out_of_range", "year_disagrees_with_byline"}  # errors first
    missing = next(finding for finding in findings if finding.check.name == "venue_missing")
    assert (missing.count, missing.total) == (2, 4)
    assert missing.share == 0.5
    assert missing.examples[0].startswith("<empty> | Graph attention networks")
    assert "50.0%" in missing.describe()


def test_the_report_states_when_nothing_is_wrong() -> None:
    assert render_audit([], 12) == ["audit of 12 records: nothing implausible found"]
    lines = render_audit(audit_records([_record(year=1066), _record(venue=None)]), 2)
    assert "3 checks tripped (2 errors, 1 warnings)" in lines[0]
    assert any("e.g." in line for line in lines)


def test_every_check_explains_what_it_breaks() -> None:
    for check in CHECKS:
        assert check.severity in {"error", "warn"}
        assert check.explain
        assert len(check.name) <= 26  # the report column width


def test_records_parsed_from_the_real_fixtures_pass_the_audit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The audit found the profile parser keeping the year inside the venue; keep it fixed.
    from dataclasses import asdict

    results = parse_result_page(RESULT_PAGE_HTML, query="fixture", start=0).results
    profile = parse_author_page(AUTHOR_PAGE_HTML, user_id="kukA0LcAAAAJ").results
    path = tmp_path / "records.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in [*results, *profile]:
            handle.write(json.dumps(asdict(record), ensure_ascii=False, default=str) + "\n")

    assert main([str(path), "--audit"]) == 0
    printed = capsys.readouterr().out
    assert "(0 errors," in printed
    assert "venue_keeps_year" not in printed


def test_a_tally_and_a_batch_audit_agree() -> None:
    # The run audits records as it writes them; the digest audits a file. Same answers.
    records = [
        _record(),
        _record(venue="521 (7553), 436-444"),
        _record(venue=None, year=None),
        _record(year=1066),
        _record(authors="A Author…"),
    ]
    tally = AuditTally()
    for record in records:
        tally.observe(record)
    assert tally.total == len(records)
    batch = audit_records(records)
    assert [(finding.check.name, finding.count) for finding in tally.findings()] == [
        (finding.check.name, finding.count) for finding in batch
    ]
    assert [finding.examples for finding in tally.findings()] == [
        finding.examples for finding in batch
    ]


def test_a_run_stays_quiet_about_the_odd_bad_record() -> None:
    tally = AuditTally()
    for _ in range(20):
        tally.observe(_record())
    tally.observe(_record(year=1066))  # one wrong record out of 21
    assert tally.alarms() == []
    assert tally.describe_alarms() == []


def test_a_field_failing_across_a_run_raises_an_alarm() -> None:
    tally = AuditTally()
    for _ in range(4):
        tally.observe(_record(venue="521 (7553), 436-444"))
    for _ in range(6):
        tally.observe(_record())
    alarms = tally.alarms()
    assert [finding.check.name for finding in alarms] == ["venue_looks_like_pages"]
    lines = tally.describe_alarms()
    assert "Scholar's layout may have changed" in lines[0]
    assert "4 of 10 records (40%)" in lines[1]
    assert any("e.g. 521 (7553), 436-444" in line for line in lines)
    assert "--self-check" in lines[-1]


def test_warnings_never_raise_an_alarm() -> None:
    # Missing fields are Scholar's doing, not a parser failure; a run must not cry wolf.
    tally = AuditTally()
    for _ in range(10):
        tally.observe(_record(venue=None, year=None, authors=None, cluster_id=None))
    assert {finding.check.name for finding in tally.findings()} == {
        "venue_missing",
        "year_missing",
        "authors_missing",
        "cluster_id_missing",
    }
    assert tally.describe_alarms() == []


def test_a_run_reports_the_alarm_after_its_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from scholar_crawler.models import ScholarResult
    from scholar_crawler.run import Outputs

    outputs = Outputs.open_for(
        out=tmp_path / "results.jsonl",
        state=tmp_path / "state.json",
        profiles=tmp_path / "profiles.jsonl",
    )
    for index in range(5):
        outputs.sink.write(
            ScholarResult(
                cluster_id=f"ID{index}",
                position=index + 1,
                title=f"Paper {index}",
                link="https://example.org/",
                resource_link=None,
                resource_type=None,
                byline="A Author - 521 (7553), 436-444, 2015 - example.org",
                authors="A Author",
                venue="521 (7553), 436-444",
                year=2015,
                snippet=None,
                cited_by_count=None,
                cited_by_url=None,
                versions_count=None,
                versions_url=None,
                related_url=None,
                citation_only=False,
                query="x",
                page_start=0,
                fetched_at="2026-09-02T00:00:00+00:00",
            )
        )
    outputs.close_and_report(None)
    printed = capsys.readouterr().out
    assert "[out] 5 new records" in printed
    assert "[audit] 1 field(s) parsed badly" in printed
    assert "venue_looks_like_pages: 5 of 5 records (100%)" in printed
