"""Offline digest: reading, merging, filtering, summarizing and writing records."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.digest import (  # noqa: E402
    build_parser,
    filter_records,
    load_records,
    main,
    merge_records,
    record_key,
    write_csv,
    write_jsonl,
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


def _write(path: Path, records: list[dict[str, object]], extra_lines: str = "") -> Path:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n" + extra_lines, encoding="utf-8"
    )
    return path


def test_records_are_keyed_like_the_crawler_sink() -> None:
    assert record_key(_record()) == "c1"
    assert record_key(_record(cluster_id=None)) == "A paper::https://example.org/a"
    assert record_key({"title": "T"}) == "T::"


def test_unreadable_lines_are_counted_not_fatal(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.jsonl", [_record()], extra_lines="{oops\n\n[1, 2]\n")
    records, malformed = load_records([path])
    assert len(records) == 1
    assert malformed == 2


def test_a_missing_input_is_reported_as_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "nope.jsonl")]) == 1
    assert "error:" in capsys.readouterr().out


def test_an_empty_input_is_reported_as_an_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("\n", encoding="utf-8")
    assert main([str(path)]) == 1
    assert "no records found" in capsys.readouterr().out


def test_the_fresher_citation_count_wins_a_merge() -> None:
    stale = _record(cited_by_count=10, extra={"follow_depth": 0, "bibtex_key": "a2020paper"})
    fresh = _record(cited_by_count=99, extra={"follow_depth": 2})
    merged, duplicates = merge_records([stale, fresh])
    assert duplicates == 1
    assert merged[0]["cited_by_count"] == 99
    # The BibTeX key from the older file survives, and the shallowest level is kept.
    assert merged[0]["extra"] == {"follow_depth": 0, "bibtex_key": "a2020paper"}


def test_a_sparse_record_does_not_overwrite_a_full_one() -> None:
    full = _record(cited_by_count=10)
    sparse = {"cluster_id": "c1", "title": "A paper", "cited_by_count": 10}
    merged, _ = merge_records([full, sparse])
    assert merged[0]["venue"] == "A Journal"
    merged_other_order, _ = merge_records([sparse, full])
    assert merged_other_order[0]["venue"] == "A Journal"


def test_distinct_works_are_both_kept_in_first_seen_order() -> None:
    merged, duplicates = merge_records([_record(), _record(cluster_id="c2", title="B paper")])
    assert duplicates == 0
    assert [record["cluster_id"] for record in merged] == ["c1", "c2"]


def test_filters_apply_together() -> None:
    records = [
        _record(cluster_id="a", year=2015, cited_by_count=5),
        _record(cluster_id="b", year=2021, cited_by_count=50),
        _record(cluster_id="c", year=2021, cited_by_count=1),
        _record(cluster_id="d", year=None, cited_by_count=500),
    ]
    kept = filter_records(records, min_citations=10, year_low=2018)
    assert [record["cluster_id"] for record in kept] == ["b"]
    assert [record["cluster_id"] for record in filter_records(records, min_citations=10)] == ["b", "d"]
    assert len(filter_records(records, year_high=2016)) == 1


def test_written_files_hold_the_kept_records(tmp_path: Path) -> None:
    records = [_record(), _record(cluster_id="c2", title="B paper")]
    jsonl = tmp_path / "nested" / "merged.jsonl"
    csv_path = tmp_path / "nested" / "merged.csv"
    assert write_jsonl(records, jsonl) == 2
    assert write_csv(records, csv_path) == 2
    assert [json.loads(line)["cluster_id"] for line in jsonl.read_text().splitlines()] == ["c1", "c2"]
    header, *rows = csv_path.read_text(encoding="utf-8").splitlines()
    assert header.startswith("position,title,authors")
    assert len(rows) == 2


def test_end_to_end_run_merges_filters_and_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = _write(
        tmp_path / "a.jsonl",
        [_record(cited_by_count=10), _record(cluster_id="c2", title="B paper", cited_by_count=2)],
    )
    second = _write(tmp_path / "b.jsonl", [_record(cited_by_count=99)])
    out = tmp_path / "merged.jsonl"
    code = main([str(first), str(second), "-o", str(out), "--min-citations", "5", "--top", "1"])
    printed = capsys.readouterr().out
    assert code == 0
    assert "3 records from 2 file(s), 1 duplicates merged, 1 filtered out" in printed
    assert "records          1" in printed
    stored = [json.loads(line) for line in out.read_text().splitlines()]
    assert [record["cited_by_count"] for record in stored] == [99]


def test_quiet_prints_only_what_was_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path / "a.jsonl", [_record()])
    assert main([str(path), "--csv", str(tmp_path / "out.csv"), "--quiet"]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert len(printed) == 1
    assert printed[0].startswith("[out] 1 rows ->")


def test_quiet_without_an_output_file_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["a.jsonl", "--quiet"]) == 1
    assert "--quiet needs --out, --csv or --bibtex" in capsys.readouterr().out


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["a.jsonl"])
    assert (args.min_citations, args.top, args.quiet) == (0, 5, False)
    assert (args.out, args.csv, args.year_from, args.year_to) == (None, None, None, None)


def test_group_table_is_printed_on_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(
        tmp_path / "in.jsonl",
        [
            _record(cluster_id="a", authors="Y Bengio, X", cited_by_count=500),
            _record(cluster_id="b", authors="Y Bengio", cited_by_count=100),
            _record(cluster_id="c", authors="G Hinton", cited_by_count=900),
        ],
    )
    assert main([str(path), "--group-by", "author", "--min-group", "2"]) == 0
    printed = capsys.readouterr().out
    assert "by author" in printed
    assert "Y Bengio" in printed
    assert "G Hinton" not in printed.split("by author")[1]  # only groups of two or more
