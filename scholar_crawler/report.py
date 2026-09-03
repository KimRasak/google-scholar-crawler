"""Turning collected records into a Markdown overview a person can read.

The JSONL and CSV files are for programs, and the terminal summary scrolls away. What a
literature search actually ends in is prose: the most-cited works, where they were published,
who wrote them, how they spread over time, and how much of it can be trusted. This module
writes that as Markdown, from records already on disk, without sending a single request.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .analysis import Group, first_author, group_records, normalize_venue, summarize
from .audit import audit_records

Record = dict[str, Any]

BAR = "▇"
"""Block used for the per-year bars; a bar chart survives copy-paste, a chart image does not."""

BAR_WIDTH = 28
"""Blocks the busiest year gets; every other year is scaled against it."""


MARKDOWN_SPECIALS = "\\`*_[]<>|"
"""Characters a Markdown reader would act on instead of printing.

Paper titles really carry them: ``*SEM 2021``, ``C*-algebras``, ``[Re] reproducibility``,
``word2vec_extended``. Unescaped, the renderer turns them into emphasis, code spans or broken
links, so the report shows a title the crawl never collected.
"""


def _counted(count: int, noun: str) -> str:
    """Write a count with its noun in the right number.

    :param count: how many.
    :param noun: the singular noun.
    :returns: the phrase, pluralized by adding ``s`` when the count is not one.
    """
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"


def _cell(text: str | None, *, limit: int = 120) -> str:
    """Make text safe for a Markdown table cell.

    :param text: raw text, possibly None.
    :param limit: characters to keep.
    :returns: the escaped, shortened text.
    """
    if not text:
        return "—"
    flattened = " ".join(text.split())
    shortened = flattened if len(flattened) <= limit else flattened[: limit - 1].rstrip() + "…"
    return "".join(f"\\{char}" if char in MARKDOWN_SPECIALS else char for char in shortened)


def _link(record: Record, *, limit: int = 120) -> str:
    """Render a record's title, linked when Scholar gave a destination.

    :param record: the record to render.
    :param limit: characters of title to keep.
    :returns: a Markdown link, or the bare title for a record with no page.
    """
    title = _cell(record.get("title"), limit=limit)
    link = record.get("link")
    # Scholar URLs carry parentheses and commas, which end an inline link early unless the
    # destination is wrapped in angle brackets.
    return f"[{title}](<{link}>)" if isinstance(link, str) and link else title


def _years(records: list[Record]) -> list[tuple[int, int]]:
    """Count records per publication year, oldest first.

    :param records: records to count.
    :returns: ``(year, count)`` pairs.
    """
    counter = Counter(record["year"] for record in records if isinstance(record.get("year"), int))
    return sorted(counter.items())


def _span(group: Group) -> str:
    """Describe a group's publication years.

    :param group: the group to describe.
    :returns: a single year, a range, or an em dash.
    """
    if group.first_year is None:
        return "—"
    if group.last_year is None or group.first_year == group.last_year:
        return str(group.first_year)
    return f"{group.first_year}–{group.last_year}"


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> list[str]:
    """Build a Markdown table.

    :param header: column titles.
    :param rows: one tuple per row, already rendered.
    :returns: the table's lines, or an empty list when there are no rows.
    """
    if not rows:
        return []
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _at_a_glance(records: list[Record]) -> list[str]:
    """Summarize the whole collection in a few bullets.

    :param records: merged records.
    :returns: the section's lines.
    """
    summary = summarize(records, top=0, venues=0)
    years = _years(records)
    span = f"{years[0][0]}–{years[-1][0]}" if years else "unknown"
    venues = {
        normalize_venue(record["venue"])
        for record in records
        if record.get("venue") and not record.get("citation_only")
    }
    authors = {first_author(record) for record in records} - {None}
    return [
        f"- **{_counted(summary.records, 'record')}**, {summary.citations:,} citations in total",
        f"- published **{span}**"
        + (f", {summary.unknown_year} without a year" if summary.unknown_year else ""),
        f"- **{_counted(len(venues), 'venue')}**, **{_counted(len(authors), 'first author')}**",
        f"- {_counted(summary.with_bibtex, 'record')} carry a BibTeX key, "
        f"{summary.citation_only} "
        + ("is" if summary.citation_only == 1 else "are")
        + " citation-only, which Scholar has no page for",
    ]


def _most_cited(records: list[Record], limit: int) -> list[str]:
    """List the most-cited works.

    :param records: merged records.
    :param limit: how many to list.
    :returns: the section's lines.
    """
    ranked = sorted(records, key=lambda record: record.get("cited_by_count") or 0, reverse=True)
    rows = [
        (
            f"{record.get('cited_by_count') or 0:,}",
            str(record.get("year") or "—"),
            _link(record),
            _cell(record.get("venue"), limit=48),
        )
        for record in ranked[:limit]
    ]
    return _table(("Citations", "Year", "Work", "Venue"), rows)


def _by_group(records: list[Record], key: str, limit: int, header: str) -> list[str]:
    """Tabulate records grouped along one dimension.

    :param records: merged records.
    :param key: grouping dimension, as :func:`group_records` understands it.
    :param limit: groups to list.
    :param header: title for the first column.
    :returns: the section's lines.
    """
    rows = [
        (
            _cell(group.label, limit=60),
            str(group.records),
            f"{group.citations:,}",
            f"{group.median_citations:,}",
            _span(group),
            _cell(group.best[1], limit=60),
        )
        for group in group_records(records, key)[:limit]
    ]
    return _table((header, "Records", "Citations", "Median", "Years", "Most cited"), rows)


def _by_year(records: list[Record]) -> list[str]:
    """Draw the per-year record counts as text bars.

    :param records: merged records.
    :returns: the section's lines.
    """
    years = _years(records)
    if not years:
        return []
    busiest = max(count for _year, count in years)
    lines = ["```"]
    for year, count in years:
        width = max(1, round(count / busiest * BAR_WIDTH))
        lines.append(f"{year}  {BAR * width} {count}")
    lines.append("```")
    return lines


def _queries(records: list[Record]) -> list[str]:
    """List what was searched to produce these records.

    :param records: merged records.
    :returns: the section's lines.
    """
    counter = Counter(record["query"] for record in records if record.get("query"))
    rows = [(_cell(query, limit=80), str(count)) for query, count in counter.most_common()]
    return _table(("Query", "Records"), rows)


def _data_quality(records: list[Record]) -> list[str]:
    """State how trustworthy the numbers above are.

    :param records: merged records.
    :returns: the section's lines.
    """
    findings = audit_records(records, examples=0)
    if not findings:
        return ["Every field parsed plausibly; nothing looked wrong."]
    rows = [
        (
            finding.check.severity,
            finding.check.name.replace("_", " "),
            f"{finding.count} ({finding.share * 100:.0f}%)",
            finding.check.explain,
        )
        for finding in findings
    ]
    return _table(("", "Check", "Records", "What it means"), rows)


def build_report(
    records: list[Record],
    *,
    title: str = "Literature overview",
    top: int = 15,
    groups: int = 8,
) -> str:
    """Build the Markdown report for a set of records.

    :param records: merged records to report on.
    :param title: heading for the document.
    :param top: how many most-cited works to list.
    :param groups: how many venues and authors to list.
    :returns: the report as Markdown text ending in a newline.
    """
    sections: list[tuple[str, list[str]]] = [
        ("At a glance", _at_a_glance(records)),
        (f"Most cited works (top {min(top, len(records))})", _most_cited(records, top)),
        ("Where this work is published", _by_group(records, "venue", groups, "Venue")),
        ("Who wrote it", _by_group(records, "author", groups, "First author")),
        ("When it was published", _by_year(records)),
        ("What was searched", _queries(records)),
        ("How much of this to trust", _data_quality(records)),
    ]
    lines = [
        f"# {title}",
        "",
        f"Built from {len(records)} records collected with "
        "[google-scholar-crawler](https://github.com/KimRasak/google-scholar-crawler). "
        "Every number below comes from what Scholar showed when the records were collected; "
        "nothing was re-fetched to build this report.",
    ]
    for heading, body in sections:
        if not body:
            continue
        lines.extend(["", f"## {heading}", ""])
        lines.extend(body)
    return "\n".join(lines) + "\n"
