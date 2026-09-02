"""Offline digest of collected records: merge, filter, export and summarize.

Crawling is slow and interruptible, so results accumulate across runs and files. This
module works only on files already on disk — it never touches Scholar — and turns a pile
of JSONL into one deduplicated set plus a readable summary.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from .analysis import (
    GROUP_KEYS,
    group_records,
    render_groups,
    render_summary,
    summarize,
)
from .audit import audit_records, render_audit
from .bibsynth import write_bibtex
from .refresh import (
    DEFAULT_REFRESH_LIMIT,
    DEFAULT_STALE_DAYS,
    rank_stale,
    refresh_ids,
    render_refresh_list,
    render_staleness,
)
from .report import build_report
from .storage import CSV_COLUMNS

Record = dict[str, Any]


def record_key(record: Record) -> str:
    """Identify a record the way the crawler's sink does.

    :param record: a stored record.
    :returns: Scholar's card id when present, otherwise title and link.
    """
    return record.get("cluster_id") or f"{record.get('title')}::{record.get('link') or ''}"


def load_records(paths: list[Path]) -> tuple[list[Record], int]:
    """Read JSONL records from every input file, in the order given.

    :param paths: JSONL files written by the crawler.
    :returns: the records read, and the number of lines that were not valid JSON objects.
    :raises FileNotFoundError: when an input file does not exist.
    """
    records: list[Record] = []
    malformed = 0
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                malformed += 1
    return records, malformed


def _filled(record: Record) -> int:
    """Count the fields a record actually carries.

    :param record: a stored record.
    :returns: number of non-empty top-level values, ignoring ``extra``.
    """
    return sum(1 for key, value in record.items() if key != "extra" and value not in (None, "", []))


def _richer(left: Record, right: Record) -> Record:
    """Choose the better of two records for the same work.

    Citation counts grow over time, so the higher count is the fresher observation; field
    count breaks ties, because a keyword result page carries more than a profile row.

    Fields the winner lacks are taken from the loser: a re-collected record refreshes the
    citation count but may come from a versions listing that carries no snippet, and dropping
    what was already known would make a refresh lose data.

    :param left: the record kept so far.
    :param right: a later record for the same work.
    :returns: the record to keep, with the other's missing fields and ``extra`` values filled
        in and the shallowest citation-graph level of the two.
    """
    def rank(record: Record) -> tuple[int, int]:
        return (record.get("cited_by_count") or -1, _filled(record))

    winner, loser = (right, left) if rank(right) > rank(left) else (left, right)
    merged = dict(winner)
    for key, value in loser.items():
        if key != "extra" and merged.get(key) in (None, "", []):
            merged[key] = value
    extra = {**(loser.get("extra") or {}), **(winner.get("extra") or {})}
    depths = [
        int(record["extra"]["follow_depth"])
        for record in (left, right)
        if (record.get("extra") or {}).get("follow_depth") is not None
    ]
    if depths:
        extra["follow_depth"] = min(depths)
    merged["extra"] = extra
    return merged


def merge_records(records: list[Record]) -> tuple[list[Record], int]:
    """Deduplicate records, keeping the richest observation of each work.

    :param records: records from one or more files.
    :returns: the deduplicated records in first-seen order, and how many were dropped.
    """
    merged: dict[str, Record] = {}
    for record in records:
        key = record_key(record)
        merged[key] = _richer(merged[key], record) if key in merged else record
    return list(merged.values()), len(records) - len(merged)


def filter_records(
    records: list[Record],
    *,
    min_citations: int = 0,
    year_low: int | None = None,
    year_high: int | None = None,
) -> list[Record]:
    """Keep the records matching every given condition.

    Records without a year are kept only when no year range is asked for, since Scholar
    leaves the year out often enough that dropping them silently would distort a summary.

    :param records: records to filter.
    :param min_citations: minimum citation count.
    :param year_low: earliest publication year to keep.
    :param year_high: latest publication year to keep.
    :returns: the matching records, order preserved.
    """
    kept: list[Record] = []
    for record in records:
        if (record.get("cited_by_count") or 0) < min_citations:
            continue
        year = record.get("year")
        if year_low is not None or year_high is not None:
            if year is None:
                continue
            if year_low is not None and year < year_low:
                continue
            if year_high is not None and year > year_high:
                continue
        kept.append(record)
    return kept


def write_jsonl(records: list[Record], path: Path) -> int:
    """Write records to a JSONL file, replacing it if it exists.

    :param records: records to store.
    :param path: destination file.
    :returns: number of records written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as target:
        for record in records:
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


def write_csv(records: list[Record], path: Path) -> int:
    """Write records to a CSV file with the crawler's column set.

    :param records: records to store.
    :param path: destination file.
    :returns: number of data rows written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    return len(records)


def build_parser() -> argparse.ArgumentParser:
    """Build the ``scholar-digest`` argument parser.

    :returns: the configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="scholar-digest",
        description="Merge, filter and summarize crawled JSONL files. Reads local files only.",
    )
    parser.add_argument("inputs", nargs="+", type=Path, metavar="FILE", help="JSONL files to read")
    parser.add_argument("-o", "--out", type=Path, help="write the merged records to this JSONL file")
    parser.add_argument("--csv", type=Path, help="write the merged records to this CSV file")
    parser.add_argument(
        "--bibtex",
        type=Path,
        metavar="FILE",
        help="build a BibTeX file from the stored fields, without contacting Scholar",
    )
    parser.add_argument(
        "--min-citations", type=int, default=0, metavar="N", help="drop records cited fewer times"
    )
    parser.add_argument("--year-from", type=int, metavar="YEAR", help="drop records published earlier")
    parser.add_argument("--year-to", type=int, metavar="YEAR", help="drop records published later")
    parser.add_argument(
        "--top", type=int, default=5, metavar="N", help="most-cited records to list (default: 5)"
    )
    parser.add_argument(
        "--group-by",
        choices=GROUP_KEYS,
        metavar="DIMENSION",
        help=f"also print a per-group table ({', '.join(GROUP_KEYS)})",
    )
    parser.add_argument(
        "--min-group",
        type=int,
        default=1,
        metavar="N",
        help="hide groups holding fewer records than this (default: 1)",
    )
    parser.add_argument(
        "--groups", type=int, default=10, metavar="N", help="groups to list (default: 10)"
    )
    parser.add_argument(
        "--report",
        type=Path,
        metavar="FILE",
        help="write a readable Markdown overview of the merged records to this file",
    )
    parser.add_argument(
        "--report-title", default="Literature overview", metavar="TEXT", help="heading for --report"
    )
    parser.add_argument(
        "--stale",
        type=float,
        nargs="?",
        const=float(DEFAULT_STALE_DAYS),
        metavar="DAYS",
        help=f"report how current the collection is, counting records older than DAYS as "
        f"stale (default: {DEFAULT_STALE_DAYS})",
    )
    parser.add_argument(
        "--refresh-list",
        type=Path,
        metavar="FILE",
        help="write the stale records' cluster ids here, most-moved first; feed the file back "
        "with scholar-crawler --clusters-file",
    )
    parser.add_argument(
        "--refresh-limit",
        type=int,
        default=DEFAULT_REFRESH_LIMIT,
        metavar="N",
        help=f"ids to write for --refresh-list; each costs one page load "
        f"(default: {DEFAULT_REFRESH_LIMIT})",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help="report fields that parsed into something implausible (missing, out of range, "
        "a venue that is really a page range) before trusting the numbers",
    )
    parser.add_argument("--quiet", action="store_true", help="print only what was written")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the digest from command-line arguments.

    :param argv: argument vector; defaults to ``sys.argv[1:]``.
    :returns: process exit code — 0 on success, 1 when an input is unusable or the flags
        would produce no output at all.
    """
    args = build_parser().parse_args(argv)
    if args.quiet and not (args.out or args.csv or args.bibtex or args.report or args.refresh_list):
        print(
            "error: --quiet needs --out, --csv, --bibtex, --report or --refresh-list, "
            "otherwise the run prints nothing",
            flush=True,
        )
        return 1
    try:
        records, malformed = load_records(args.inputs)
    except OSError as error:
        print(f"error: {error}", flush=True)
        return 1
    if not records:
        print("error: no records found in the given files", flush=True)
        return 1

    merged, duplicates = merge_records(records)
    kept = filter_records(
        merged,
        min_citations=args.min_citations,
        year_low=args.year_from,
        year_high=args.year_to,
    )
    if not args.quiet:
        print(
            f"[in] {len(records)} records from {len(args.inputs)} file(s), "
            f"{duplicates} duplicates merged, {len(merged) - len(kept)} filtered out"
            + (f", {malformed} unreadable lines" if malformed else ""),
            flush=True,
        )
        for line in render_summary(summarize(kept, top=args.top)):
            print(f"  {line}", flush=True)
        if args.audit:
            for line in render_audit(audit_records(kept), len(kept)):
                print(f"  {line}", flush=True)
        if args.stale is not None:
            for line in render_staleness(kept, days=args.stale, top=args.top or 10):
                print(f"  {line}", flush=True)
        if args.group_by:
            groups = group_records(kept, args.group_by, min_size=args.min_group)
            for line in render_groups(groups, args.group_by, limit=args.groups):
                print(f"  {line}", flush=True)
    if args.out:
        print(f"[out] {write_jsonl(kept, args.out)} records -> {args.out}", flush=True)
    if args.csv:
        print(f"[out] {write_csv(kept, args.csv)} rows -> {args.csv}", flush=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        markdown = build_report(kept, title=args.report_title, top=args.top or 15)
        args.report.write_text(markdown, encoding="utf-8")
        counted = f"{len(kept)} record" + ("" if len(kept) == 1 else "s")
        print(f"[out] report on {counted} -> {args.report}", flush=True)
    if args.refresh_list:
        days = args.stale if args.stale is not None else float(DEFAULT_STALE_DAYS)
        aged = rank_stale(kept, days=days)
        lines = render_refresh_list(aged, limit=args.refresh_limit)
        args.refresh_list.parent.mkdir(parents=True, exist_ok=True)
        args.refresh_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written = len(refresh_ids(aged, limit=args.refresh_limit))
        print(
            f"[out] {written} id(s) to re-list -> {args.refresh_list} "
            f"(of {len(aged)} records older than {days:g} days)",
            flush=True,
        )
    if args.bibtex:
        report = write_bibtex(kept, args.bibtex)
        print(
            f"[out] {report.written} entries -> {args.bibtex} ({report.describe()})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
