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
from .collection import SUFFIX, Delta, collection_files, compare, render_delta
from .graph import build_graph, render_network
from .machine import document, emit, failure, human_lines_to_stderr, version
from .models import record_key
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

DEFAULT_TOP = 5
"""Entries in every printed list, so one number governs what the terminal shows."""

DEFAULT_REPORT_TITLE = "Literature overview"
"""Heading of the Markdown report when the caller names none."""

DEFAULT_REPORT_TOP = 15
"""Records listed in the Markdown report, which has room for more than the terminal."""


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


def _resolve_inputs(args: argparse.Namespace) -> list[Path]:
    """Decide which files this run reads.

    :param args: parsed arguments.
    :returns: the input files, collection first and named files after.
    :raises NotADirectoryError: when ``--collection`` is not a directory.
    :raises ValueError: when nothing was given to read, or a collection holds no result files.
    """
    if args.collection is None:
        if not args.inputs:
            raise ValueError("give some JSONL files to read, or a folder with --collection DIR")
        return args.inputs
    written = [path for path in (args.out, args.since) if path is not None]
    found = collection_files(args.collection, exclude=written)
    if not found and not args.inputs:
        raise ValueError(
            f"{args.collection} holds no {SUFFIX} files to read"
            + (f" besides {', '.join(str(path) for path in written)}" if written else "")
        )
    return found + args.inputs


def build_parser() -> argparse.ArgumentParser:
    """Build the ``scholar-digest`` argument parser.

    :returns: the configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="scholar-digest",
        description="Merge, filter and summarize crawled JSONL files. Reads local files only.",
        epilog=(
            "Records are merged and deduplicated first; every report and file below covers the "
            "same selected set."
        ),
    )
    parser.add_argument("inputs", nargs="*", type=Path, metavar="FILE", help="JSONL files to read")
    talking = parser.add_argument_group(
        "how this command reports", "who reads the output: a person, a file, or a program"
    )
    talking.add_argument("--quiet", action="store_true", help="print only what was written")
    talking.add_argument(
        "--json",
        action="store_true",
        help="print one JSON object on stdout — counts, the overview, the comparison and the "
        "kept records — with every human line on stderr instead",
    )
    talking.add_argument(
        "--version", action="store_true", help="print the installed version and stop"
    )

    collection = parser.add_argument_group(
        "collection", "treat a directory as one collection instead of listing files by hand"
    )
    collection.add_argument(
        "--collection",
        type=Path,
        metavar="DIR",
        help="read every .jsonl in this directory, excluding the files this run writes",
    )
    collection.add_argument(
        "--since",
        type=Path,
        metavar="FILE",
        help="report what changed against an earlier merged file: new works, works no longer "
        "here, and citation counts that moved",
    )

    selection = parser.add_argument_group(
        "selection", "which of the merged records everything below covers"
    )
    selection.add_argument(
        "--min-citations", type=int, default=0, metavar="N", help="drop records cited fewer times"
    )
    selection.add_argument("--year-from", type=int, metavar="YEAR", help="drop records published earlier")
    selection.add_argument("--year-to", type=int, metavar="YEAR", help="drop records published later")

    printed = parser.add_argument_group(
        "printed reports", "read the collection in the terminal; these write nothing"
    )
    printed.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        metavar="N",
        help=f"entries in every printed list: most cited, stale, cited from inside "
        f"(default: {DEFAULT_TOP}; 0 lists none)",
    )
    printed.add_argument(
        "--group-by",
        choices=GROUP_KEYS,
        metavar="DIMENSION",
        help=f"also print a per-group table ({', '.join(GROUP_KEYS)})",
    )
    printed.add_argument(
        "--groups", type=int, default=10, metavar="N", help="groups to list (default: 10)"
    )
    printed.add_argument(
        "--audit",
        action="store_true",
        help="report fields that parsed into something implausible (missing, out of range, "
        "a venue that is really a page range) before trusting the numbers",
    )
    printed.add_argument(
        "--network",
        action="store_true",
        help="report the citation graph the records already carry: who cites whom inside the "
        "collection, its components, and which records stand alone",
    )
    printed.add_argument(
        "--stale",
        type=float,
        nargs="?",
        const=float(DEFAULT_STALE_DAYS),
        metavar="DAYS",
        help=f"report how current the collection is, counting records older than DAYS as "
        f"stale (default: {DEFAULT_STALE_DAYS})",
    )

    written = parser.add_argument_group(
        "written outputs", "hand the collection to something else: a spreadsheet, LaTeX, Gephi"
    )
    written.add_argument("-o", "--out", type=Path, help="write the merged records to this JSONL file")
    written.add_argument("--csv", type=Path, help="write the merged records to this CSV file")
    written.add_argument(
        "--bibtex",
        type=Path,
        metavar="FILE",
        help="build a BibTeX file from the stored fields, without contacting Scholar",
    )
    written.add_argument(
        "--report",
        type=Path,
        metavar="FILE",
        help="write a readable Markdown overview of the merged records to this file",
    )
    written.add_argument(
        "--report-title",
        default=DEFAULT_REPORT_TITLE,
        metavar="TEXT",
        help=f"heading for --report (default: {DEFAULT_REPORT_TITLE!r})",
    )
    written.add_argument(
        "--report-top",
        type=int,
        default=DEFAULT_REPORT_TOP,
        metavar="N",
        help=f"records listed in --report, which has room for more than the terminal "
        f"(default: {DEFAULT_REPORT_TOP})",
    )
    written.add_argument(
        "--refresh-list",
        type=Path,
        metavar="FILE",
        help="write the stale records' cluster ids here, most-moved first; feed the file back "
        "with scholar-crawler --clusters-file",
    )
    written.add_argument(
        "--refresh-limit",
        type=int,
        default=DEFAULT_REFRESH_LIMIT,
        metavar="N",
        help=f"ids to write for --refresh-list; each costs one page load "
        f"(default: {DEFAULT_REFRESH_LIMIT})",
    )
    return parser


def _fail(kind: str, message: str, *next_steps: str) -> tuple[int, dict[str, object]]:
    """Report a refusal in both registers: a line for a person, a document for a program.

    :param kind: stable machine name of the refusal.
    :param message: the line already printed for a person.
    :param next_steps: concrete actions, most useful first.
    :returns: the exit code and the JSON document.
    """
    print(f"error: {message}", flush=True)
    return 1, document(
        tool="scholar-digest",
        exit_code=1,
        counts={},
        error=failure(kind, message, next_steps),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the digest from command-line arguments.

    :param argv: argument vector; defaults to ``sys.argv[1:]``.
    :returns: process exit code — 0 on success, 1 when an input is unusable or the flags
        would produce no output at all.
    """
    args = build_parser().parse_args(argv)
    if args.version:
        print(f"scholar-digest {version()}", flush=True)
        return 0
    with human_lines_to_stderr(args.json):
        exit_code, payload = _run(args)
    if args.json:
        emit(payload)
    return exit_code


def _run(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Digest whatever the arguments describe.

    :param args: parsed arguments.
    :returns: the exit code and the JSON document describing the run.
    """
    try:
        args.inputs = _resolve_inputs(args)
    except (NotADirectoryError, ValueError) as error:
        return _fail("bad_inputs", str(error))
    if args.quiet and not (
        args.out or args.csv or args.bibtex or args.report or args.refresh_list
    ):
        return _fail(
            "usage",
            "--quiet needs --out, --csv, --bibtex, --report or --refresh-list, "
            "otherwise the run prints nothing",
        )
    try:
        records, malformed = load_records(args.inputs)
    except OSError as error:
        return _fail("unreadable_input", str(error))
    if not records:
        return _fail("no_records", "no records found in the given files")

    merged, duplicates = merge_records(records)
    earlier: list[Record] | None = None
    if args.since is not None:
        try:
            earlier, _ = load_records([args.since])
        except FileNotFoundError:
            return _fail("missing_since", f"{args.since}: no earlier merge to compare against")
        except OSError as error:
            return _fail("unreadable_input", str(error))
    kept = filter_records(
        merged,
        min_citations=args.min_citations,
        year_low=args.year_from,
        year_high=args.year_to,
    )
    delta = compare(earlier, kept) if earlier is not None else None
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
        if args.network:
            for line in render_network(build_graph(records, kept), top=args.top):
                print(f"  {line}", flush=True)
        if delta is not None:
            for line in render_delta(delta, top=args.top, since=args.since):
                print(f"  {line}", flush=True)
        if args.stale is not None:
            for line in render_staleness(kept, days=args.stale, top=args.top):
                print(f"  {line}", flush=True)
        if args.group_by:
            groups = group_records(kept, args.group_by)
            for line in render_groups(groups, args.group_by, limit=args.groups):
                print(f"  {line}", flush=True)
    written: dict[str, Path | None] = {}
    if args.out:
        print(f"[out] {write_jsonl(kept, args.out)} records -> {args.out}", flush=True)
        written["records"] = args.out
    if args.csv:
        print(f"[out] {write_csv(kept, args.csv)} rows -> {args.csv}", flush=True)
        written["csv"] = args.csv
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        markdown = build_report(kept, title=args.report_title, top=args.report_top)
        args.report.write_text(markdown, encoding="utf-8")
        counted = f"{len(kept)} record" + ("" if len(kept) == 1 else "s")
        print(f"[out] report on {counted} -> {args.report}", flush=True)
        written["report"] = args.report
    if args.refresh_list:
        days = args.stale if args.stale is not None else float(DEFAULT_STALE_DAYS)
        aged = rank_stale(kept, days=days)
        lines = render_refresh_list(aged, limit=args.refresh_limit)
        args.refresh_list.parent.mkdir(parents=True, exist_ok=True)
        args.refresh_list.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ids = len(refresh_ids(aged, limit=args.refresh_limit))
        print(
            f"[out] {ids} id(s) to re-list -> {args.refresh_list} "
            f"(of {len(aged)} records older than {days:g} days)",
            flush=True,
        )
        written["refresh_list"] = args.refresh_list
    if args.bibtex:
        report = write_bibtex(kept, args.bibtex)
        print(
            f"[out] {report.written} entries -> {args.bibtex} ({report.describe()})",
            flush=True,
        )
        written["bibtex"] = args.bibtex
    return 0, document(
        tool="scholar-digest",
        exit_code=0,
        counts={
            "records": len(kept),
            "read": len(records),
            "files": len(args.inputs),
            "duplicates": duplicates,
            "filtered_out": len(merged) - len(kept),
            "unreadable_lines": malformed,
        },
        files=written,
        records=kept,
        extra=_sections(kept, delta, top=args.top),
    )


def _sections(
    kept: list[Record], delta: Delta | None, *, top: int
) -> dict[str, object]:
    """Assemble the digest-specific sections of the document.

    :param kept: the records that survived filtering.
    :param delta: the comparison against an earlier merge, when one was asked for.
    :param top: how many most-cited records to name.
    :returns: the overview, and the comparison when there is one.
    """
    overview = summarize(kept, top=top)
    sections: dict[str, object] = {
        "overview": {
            "records": overview.records,
            "citations": overview.citations,
            "with_bibtex": overview.with_bibtex,
            "citation_only": overview.citation_only,
            "unknown_year": overview.unknown_year,
            "years": [{"year": year, "records": count} for year, count in overview.years],
            "venues": [{"venue": venue, "records": count} for venue, count in overview.venues],
            "most_cited": [
                {"citations": citations, "year": year, "title": title}
                for citations, year, title in overview.top
            ],
        }
    }
    if delta is not None:
        sections["delta"] = {
            "before": delta.before_total,
            "after": delta.after_total,
            "added": delta.added,
            "gone": delta.gone,
            "unchanged": delta.same,
            "citations_gained": delta.citations_gained,
            "moved": [
                {"title": item.label, "before": item.before, "after": item.after,
                 "change": item.change}
                for item in delta.moved
            ],
        }
    return sections


if __name__ == "__main__":
    raise SystemExit(main())
