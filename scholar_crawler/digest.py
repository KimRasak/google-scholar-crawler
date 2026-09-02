"""Offline digest of collected records: merge, filter, export and summarize.

Crawling is slow and interruptible, so results accumulate across runs and files. This
module works only on files already on disk — it never touches Scholar — and turns a pile
of JSONL into one deduplicated set plus a readable summary.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    :param left: the record kept so far.
    :param right: a later record for the same work.
    :returns: the record to keep, with the other's ``extra`` values filled in and the
        shallowest citation-graph level of the two.
    """
    def rank(record: Record) -> tuple[int, int]:
        return (record.get("cited_by_count") or -1, _filled(record))

    winner, loser = (right, left) if rank(right) > rank(left) else (left, right)
    merged = dict(winner)
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


@dataclass(slots=True)
class Summary:
    """Aggregate view of a set of records.

    :param records: number of records summarized.
    :param citations: sum of citation counts.
    :param with_bibtex: records that already carry a BibTeX key.
    :param citation_only: records Scholar has no page for.
    :param years: record count per publication year, newest first.
    :param unknown_year: records without a year.
    :param levels: record count per citation-graph level.
    :param venues: the most common venues with their counts.
    :param top: the most-cited records as (citations, year, title).
    """

    records: int
    citations: int
    with_bibtex: int
    citation_only: int
    years: list[tuple[int, int]] = field(default_factory=list)
    unknown_year: int = 0
    levels: list[tuple[int, int]] = field(default_factory=list)
    venues: list[tuple[str, int]] = field(default_factory=list)
    top: list[tuple[int, int | None, str]] = field(default_factory=list)


def summarize(records: list[Record], *, top: int = 5, venues: int = 5) -> Summary:
    """Aggregate records into the numbers worth printing.

    :param records: records to summarize.
    :param top: how many most-cited records to list.
    :param venues: how many most common venues to list.
    :returns: the aggregate view.
    """
    years = Counter(record["year"] for record in records if record.get("year"))
    levels = Counter(int((record.get("extra") or {}).get("follow_depth", 0)) for record in records)
    venue_counts = Counter(
        record["venue"] for record in records if record.get("venue") and not record.get("citation_only")
    )
    ranked = sorted(records, key=lambda record: record.get("cited_by_count") or 0, reverse=True)
    return Summary(
        records=len(records),
        citations=sum(record.get("cited_by_count") or 0 for record in records),
        with_bibtex=sum(1 for record in records if (record.get("extra") or {}).get("bibtex_key")),
        citation_only=sum(1 for record in records if record.get("citation_only")),
        years=sorted(years.items(), reverse=True),
        unknown_year=sum(1 for record in records if not record.get("year")),
        levels=sorted(levels.items()),
        venues=venue_counts.most_common(venues),
        top=[
            (record.get("cited_by_count") or 0, record.get("year"), record.get("title") or "")
            for record in ranked[:top]
        ],
    )


def render_summary(summary: Summary) -> list[str]:
    """Format a summary as printable lines.

    :param summary: the aggregate view.
    :returns: one line per fact, without trailing newlines.
    """
    lines = [
        f"records          {summary.records}",
        f"citations        {summary.citations} total",
        f"bibtex keys      {summary.with_bibtex}",
        f"citation-only    {summary.citation_only}",
        f"unknown year     {summary.unknown_year}",
    ]
    if summary.years:
        span = ", ".join(f"{year}:{count}" for year, count in summary.years[:12])
        lines.append(f"years            {span}")
    if len(summary.levels) > 1:
        span = ", ".join(f"L{level}:{count}" for level, count in summary.levels)
        lines.append(f"graph levels     {span}")
    for index, (venue, count) in enumerate(summary.venues):
        lines.append(f"{'venues' if index == 0 else '':<16} {count:>4}  {venue}")
    for index, (citations, year, title) in enumerate(summary.top):
        year_text = str(year) if year else "----"
        lines.append(f"{'most cited' if index == 0 else '':<16} {citations:>6}  {year_text}  {title}")
    return lines


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
        "--min-citations", type=int, default=0, metavar="N", help="drop records cited fewer times"
    )
    parser.add_argument("--year-from", type=int, metavar="YEAR", help="drop records published earlier")
    parser.add_argument("--year-to", type=int, metavar="YEAR", help="drop records published later")
    parser.add_argument(
        "--top", type=int, default=5, metavar="N", help="most-cited records to list (default: 5)"
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
    if args.quiet and not (args.out or args.csv):
        print("error: --quiet needs --out or --csv, otherwise the run prints nothing", flush=True)
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
    if args.out:
        print(f"[out] {write_jsonl(kept, args.out)} records -> {args.out}", flush=True)
    if args.csv:
        print(f"[out] {write_csv(kept, args.csv)} rows -> {args.csv}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
