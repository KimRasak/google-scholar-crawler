"""Staleness and refresh targets: what age implies, and what the next crawl should re-list."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import build_parser, build_targets  # noqa: E402
from scholar_crawler.digest import main, merge_records  # noqa: E402
from scholar_crawler.refresh import (  # noqa: E402
    DEFAULT_STALE_DAYS,
    age_in_days,
    rank_stale,
    refresh_id,
    refresh_ids,
    render_refresh_list,
    render_staleness,
    undated,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def _record(*, days_old: float | None, citations: int = 10, **overrides: Any) -> dict[str, Any]:
    """Build a stored record collected ``days_old`` days before :data:`NOW`."""
    record: dict[str, Any] = {
        "cluster_id": overrides.pop("cluster_id", f"card{citations}{days_old}"),
        "title": "Graph attention networks",
        "cited_by_count": citations,
        "cited_by_url": "https://scholar.google.com/scholar?cites=1234567890",
        "versions_url": "https://scholar.google.com/scholar?cluster=9876543210",
        "fetched_at": "" if days_old is None else (NOW - timedelta(days=days_old)).isoformat(),
    }
    record.update(overrides)
    return record


def test_age_is_read_from_the_stamp_the_parser_wrote() -> None:
    assert age_in_days(_record(days_old=30), NOW) == pytest.approx(30.0)
    assert age_in_days(_record(days_old=0.5), NOW) == pytest.approx(0.5)


def test_a_record_without_a_usable_stamp_has_no_age() -> None:
    assert age_in_days(_record(days_old=None), NOW) is None
    assert age_in_days({"fetched_at": "last tuesday"}, NOW) is None
    assert age_in_days({}, NOW) is None


def test_a_naive_timestamp_is_read_as_utc_rather_than_rejected() -> None:
    naive = {"fetched_at": (NOW - timedelta(days=10)).replace(tzinfo=None).isoformat()}
    assert age_in_days(naive, NOW) == pytest.approx(10.0)


def test_the_refresh_id_comes_from_the_links_a_listing_accepts() -> None:
    # cluster_id is Scholar's per-card data-cid, which no listing accepts as a filter.
    assert refresh_id(_record(days_old=1, cluster_id="uQm0ZqKg100J")) == "9876543210"
    assert refresh_id(_record(days_old=1, versions_url=None)) == "1234567890"
    assert refresh_id(_record(days_old=1, versions_url=None, cited_by_url=None)) is None
    assert refresh_id({"versions_url": "https://example.org/paper"}) is None


def test_only_records_older_than_the_threshold_are_listed() -> None:
    records = [_record(days_old=5), _record(days_old=45), _record(days_old=None)]
    stale = rank_stale(records, days=30, now=NOW)
    assert [round(item.age_days) for item in stale] == [45]
    assert undated(records) == 1


def test_the_report_lists_as_many_candidates_as_asked_for() -> None:
    records = [_record(days_old=100 + index, citations=index + 1) for index in range(6)]
    listed = [line for line in render_staleness(records, days=30, now=NOW, top=2) if "citations" in line]
    assert len(listed) == 2, "a longer list than asked for buries the ranking it just computed"
    full = [line for line in render_staleness(records, days=30, now=NOW) if "citations" in line]
    assert len(full) == 6, "the default list shows every stale record here"


def test_the_most_cited_stale_records_come_first() -> None:
    # Age alone would put the older, barely-cited paper first; its count cannot have moved.
    quiet = _record(days_old=400, citations=2, cluster_id="quiet")
    busy = _record(days_old=120, citations=40000, cluster_id="busy")
    ranked = rank_stale([quiet, busy], days=30, now=NOW)
    assert [item.record["cluster_id"] for item in ranked] == ["busy", "quiet"]


def test_two_equally_cited_records_are_ordered_by_age() -> None:
    old = _record(days_old=300, citations=100, cluster_id="old")
    new = _record(days_old=60, citations=100, cluster_id="new")
    ranked = rank_stale([new, old], days=30, now=NOW)
    assert [item.record["cluster_id"] for item in ranked] == ["old", "new"]


def test_the_report_states_the_span_the_share_and_what_can_be_re_listed() -> None:
    records = [
        _record(days_old=1, cluster_id="fresh"),
        _record(days_old=100, citations=500, cluster_id="stale"),
        _record(days_old=200, citations=5, cluster_id="orphan", versions_url=None, cited_by_url=None),
        _record(days_old=None, cluster_id="undated"),
    ]
    lines = render_staleness(records, days=30, now=NOW)
    assert "4 records, collected between 200 and 1 days ago; 1 carry no timestamp" in lines[0]
    assert "2 older than 30 days" in lines[1]
    assert "1 of those can be re-listed by id, one page load each; 1 would need their query" in lines[2]
    assert any("--cluster 9876543210" in line for line in lines)
    assert any("re-run its query" in line for line in lines)


def test_one_crawl_stamps_one_age_and_the_report_stops_claiming_a_span() -> None:
    # The normal state of a new collection: every record carries the same fetched_at, so the
    # ranking below is citation count and the report must not imply age played a part.
    records = [
        _record(days_old=100, citations=500, cluster_id="a", versions_url="?cluster=111"),
        _record(days_old=100, citations=5, cluster_id="b", versions_url="?cluster=222"),
    ]
    lines = render_staleness(records, days=30, now=NOW)

    assert lines[0] == "2 records, all collected 100 days ago"
    assert "between" not in lines[0]
    assert lines[3] == "all the same age, so this order is by citation count, not by what moved"
    assert "111" in lines[4] and "222" in lines[5]


def test_a_fully_current_collection_says_so_without_a_list() -> None:
    lines = render_staleness([_record(days_old=2)], days=30, now=NOW)
    assert "1 older than 30 days" not in lines
    assert lines[1].startswith("0 older than 30 days")
    assert len(lines) == 2  # nothing to list


def test_a_collection_with_no_timestamps_at_all_says_that_plainly() -> None:
    lines = render_staleness([_record(days_old=None), _record(days_old=None)], days=30, now=NOW)
    assert lines == ["none of the 2 records carries a collection timestamp"]


def test_the_refresh_file_is_ids_plus_comments_and_names_the_command() -> None:
    records = [
        _record(days_old=100, citations=500, cluster_id="a", versions_url="?cluster=111"),
        _record(days_old=90, citations=400, cluster_id="b", versions_url="?cluster=222"),
        _record(days_old=80, citations=300, cluster_id="c", versions_url="?cluster=333"),
    ]
    ranked = rank_stale(records, days=30, now=NOW)
    lines = render_refresh_list(ranked, path=Path("out/refresh.txt"), limit=2)
    # The header used to say "<this file>", a placeholder only the reader could resolve.
    assert lines[1] == "# feed this back with: scholar-crawler --clusters-file out/refresh.txt -p 1"
    assert [line for line in lines if not line.startswith("#")] == ["111", "222"]
    assert any("100 days old: Graph attention networks" in line for line in lines)


def test_one_id_is_listed_once_even_when_several_records_share_it() -> None:
    shared = "https://scholar.google.com/scholar?cluster=555"
    records = [
        _record(days_old=100, cluster_id="a", versions_url=shared),
        _record(days_old=90, cluster_id="b", versions_url=shared),
    ]
    assert refresh_ids(rank_stale(records, days=30, now=NOW), limit=10) == ["555"]


def test_the_digest_writes_a_refresh_file_the_crawler_can_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "records.jsonl"
    stale = _record(days_old=200, citations=900, cluster_id="stale", versions_url="?cluster=4242")
    fresh = _record(days_old=1, cluster_id="fresh")
    source.write_text(
        "".join(json.dumps(record) + "\n" for record in (stale, fresh)), encoding="utf-8"
    )
    destination = tmp_path / "nested" / "refresh.txt"

    assert main([str(source), "--stale", "30", "--refresh-list", str(destination)]) == 0
    printed = capsys.readouterr().out
    assert "older than 30 days" in printed
    assert f"[out] 1 id(s) to re-list -> {destination}" in printed

    # The crawler reads that file back as targets, which is the point of the format.
    args = build_parser().parse_args(["--clusters-file", str(destination), "-p", "1"])
    listings, authors = build_targets(args)
    assert [request.cluster for request in listings] == ["4242"]
    assert authors == []


def test_stale_defaults_to_a_month_when_given_no_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "records.jsonl"
    source.write_text(json.dumps(_record(days_old=1)) + "\n", encoding="utf-8")
    assert main([str(source), "--stale"]) == 0
    assert f"older than {DEFAULT_STALE_DAYS} days" in capsys.readouterr().out


def test_a_refreshed_record_keeps_what_the_older_copy_knew() -> None:
    # A versions listing carries no snippet; merging must not lose the one already collected.
    old = _record(days_old=200, citations=100, cluster_id="same")
    old["snippet"] = "We propose a new architecture"
    old["venue"] = "ICLR"
    refreshed = _record(days_old=0, citations=140, cluster_id="same")
    refreshed["snippet"] = ""
    refreshed["venue"] = None

    merged, duplicates = merge_records([old, refreshed])
    assert duplicates == 1
    assert merged[0]["cited_by_count"] == 140  # the fresher count wins
    assert merged[0]["snippet"] == "We propose a new architecture"
    assert merged[0]["venue"] == "ICLR"
    assert age_in_days(merged[0], NOW) == pytest.approx(0.0, abs=0.01)
