"""Reading collected records: overview counts and per-dimension grouping.

Nothing here touches Scholar or the filesystem; these are the numbers the digest prints
once records have been merged and filtered.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Any

Record = dict[str, Any]


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
        normalize_venue(record["venue"])
        for record in records
        if record.get("venue") and not record.get("citation_only")
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


GROUP_KEYS = ("author", "venue", "year", "level")
"""Dimensions records can be grouped along."""

ARXIV_VENUE = re.compile(r"^arxiv preprint\b", re.IGNORECASE)
"""Scholar spells arXiv venues out with the identifier, which would split every preprint."""

VOLUME_TAIL = re.compile(r"\s+\d+\s*(\([^)]*\))?\s*(,.*)?$")
"""Profile rows append volume, issue, pages and year to the journal name."""


def first_author(record: Record) -> str | None:
    """Read the first author of a record.

    :param record: a stored record.
    :returns: the first author as Scholar abbreviates them, or None when the byline is empty.
    """
    line = (record.get("authors") or record.get("byline") or "").split(" - ")[0]
    first = line.split(",")[0].strip().strip("…").strip()
    return first or None


def normalize_venue(venue: str) -> str:
    """Collapse the spellings Scholar uses for one venue.

    Two spellings would otherwise split one venue: every arXiv preprint carries its own
    identifier, and profile rows append volume, issue, pages and year to the journal name.

    :param venue: the venue as parsed.
    :returns: the venue used for grouping.
    """
    cleaned = venue.strip().strip("…").strip(" ,.")
    if ARXIV_VENUE.match(cleaned):
        return "arXiv preprint"
    trimmed = VOLUME_TAIL.sub("", cleaned).strip(" ,.")
    return trimmed or cleaned


def group_label(record: Record, key: str) -> str | None:
    """Read the grouping label of a record along one dimension.

    :param record: a stored record.
    :param key: one of :data:`GROUP_KEYS`.
    :returns: the label, or None when the record carries nothing for this dimension.
    :raises ValueError: when ``key`` is not a known dimension.
    """
    if key == "author":
        return first_author(record)
    if key == "venue":
        venue = record.get("venue")
        return normalize_venue(venue) if venue and not record.get("citation_only") else None
    if key == "year":
        year = record.get("year")
        return str(year) if year else None
    if key == "level":
        return f"L{int((record.get('extra') or {}).get('follow_depth', 0))}"
    raise ValueError(f"unknown group key {key!r}; choose from {', '.join(GROUP_KEYS)}")


@dataclass(slots=True)
class Group:
    """Records sharing one label, with the numbers worth comparing across groups.

    :param label: the shared author, venue, year or graph level.
    :param records: how many records fall in the group.
    :param citations: sum of citation counts.
    :param median_citations: median citation count, which a single famous paper cannot skew.
    :param first_year: earliest publication year present, when any.
    :param last_year: latest publication year present, when any.
    :param best: the most-cited record as (citations, title).
    """

    label: str
    records: int
    citations: int
    median_citations: int
    first_year: int | None
    last_year: int | None
    best: tuple[int, str]


def group_records(records: list[Record], key: str, *, min_size: int = 1) -> list[Group]:
    """Group records along one dimension, most-cited group first.

    :param records: records to group.
    :param key: one of :data:`GROUP_KEYS`.
    :param min_size: drop groups holding fewer records than this.
    :returns: the groups, ordered by total citations then record count.
    :raises ValueError: when ``key`` is not a known dimension.
    """
    # Grouping is case-insensitive — Scholar writes "nature" and "Nature" for one journal —
    # while the first spelling seen is kept for display.
    buckets: dict[str, tuple[str, list[Record]]] = {}
    for record in records:
        label = group_label(record, key)
        if label:
            display, members = buckets.setdefault(label.casefold(), (label, []))
            members.append(record)
    groups = []
    for label, members in buckets.values():
        if len(members) < min_size:
            continue
        counts = [record.get("cited_by_count") or 0 for record in members]
        years = sorted(record["year"] for record in members if record.get("year"))
        best = max(members, key=lambda record: record.get("cited_by_count") or 0)
        groups.append(
            Group(
                label=label,
                records=len(members),
                citations=sum(counts),
                median_citations=int(median(counts)),
                first_year=years[0] if years else None,
                last_year=years[-1] if years else None,
                best=(best.get("cited_by_count") or 0, best.get("title") or ""),
            )
        )
    return sorted(groups, key=lambda group: (group.citations, group.records), reverse=True)


def render_groups(groups: list[Group], key: str, *, limit: int = 10) -> list[str]:
    """Format groups as an aligned table.

    :param groups: the groups to print, already ordered.
    :param key: the dimension they were grouped along, used in the header.
    :param limit: how many groups to list.
    :returns: a header line followed by one line per group.
    """
    if not groups:
        return [f"by {key}: nothing to group"]
    shown = groups[:limit]
    width = min(max(max(len(group.label) for group in shown), 12), 40)
    lines = [
        f"  {'by ' + key:<{width}} {'count':>5} {'citations':>10} "
        f"{'median':>7}  {'years':<9}  most cited"
    ]
    for group in shown:
        span = ""
        if group.first_year:
            span = str(group.first_year)
            if group.last_year and group.last_year != group.first_year:
                span += f"-{group.last_year}"
        lines.append(
            f"  {group.label[:width]:<{width}} {group.records:>5} {group.citations:>10} "
            f"{group.median_citations:>7}  {span:<9}  {group.best[1][:44]}"
        )
    if len(groups) > limit:
        lines.append(f"  ... and {len(groups) - limit} more groups")
    return lines
