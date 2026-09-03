"""Deciding which stored records are worth collecting again.

A literature set is not collected once. Citation counts move, records arrive from different
queries months apart, and nothing in a JSONL file says which parts of it are current. Every
record carries the ``fetched_at`` stamp the parser wrote, so age is knowable offline; this
module turns that into an answer to "what should I re-fetch, and what does that cost".

Nothing here contacts Scholar. It produces the list of ids a later crawl consumes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .text import clip
from .urls import parse_cluster_id

Record = dict[str, Any]

DEFAULT_STALE_DAYS = 30
"""Age at which a stored citation count is treated as worth checking again."""

DEFAULT_REFRESH_LIMIT = 20
"""How many refresh targets to list by default; each one costs a page load."""


def age_in_days(record: Record, now: datetime) -> float | None:
    """Read how long ago a record was collected.

    :param record: a stored record.
    :param now: the moment to measure against.
    :returns: age in days, or None when the record carries no usable timestamp.
    """
    stamp = record.get("fetched_at")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        collected = datetime.fromisoformat(stamp)
    except ValueError:  # a hand-edited or truncated timestamp
        return None
    if collected.tzinfo is None:
        collected = collected.replace(tzinfo=timezone.utc)
    return (now - collected).total_seconds() / 86400.0


def refresh_id(record: Record) -> str | None:
    """Find the id that re-lists this exact work.

    ``versions_url`` carries the numeric cluster id, and ``--cluster`` on that id lists the
    work itself, so one page load refreshes the record. The ``cluster_id`` field is Scholar's
    per-card ``data-cid``, which no listing accepts.

    :param record: a stored record.
    :returns: the numeric cluster id, or None when the record carries no such link.
    """
    for field in ("versions_url", "cited_by_url"):
        value = record.get(field)
        if isinstance(value, str) and value:
            try:
                return parse_cluster_id(value)
            except ValueError:  # a link that carries no id, e.g. a rewritten URL
                continue
    return None


@dataclass(slots=True, frozen=True)
class Aged:
    """One record, with what its age implies.

    :param record: the stored record.
    :param age_days: how long ago it was collected.
    :param pressure: how much its stored citation count is likely to have moved.
    :param target: the numeric cluster id that re-lists it, when it has one.
    """

    record: Record
    age_days: float
    pressure: float
    target: str | None

    def describe(self) -> str:
        """Format the record as one line of the refresh list.

        :returns: age, citation count, refreshability and title.
        """
        citations = self.record.get("cited_by_count")
        counted = f"{citations:,}" if isinstance(citations, int) else "?"
        how = f"--cluster {self.target}" if self.target else "no id; re-run its query"
        title = " ".join(str(self.record.get("title") or "untitled").split())
        return f"{self.age_days:>5.0f}d  {counted:>9} citations  {how:<28} {clip(title, 70)}"


def _pressure(age_days: float, citations: int | None) -> float:
    """Estimate how far a stored citation count has drifted.

    Citations accrue roughly in proportion to the count already held, so age alone ranks a
    three-citation paper level with a forty-thousand-citation one. Weighting age by the
    logarithm of the count puts the records whose numbers actually moved first. This orders a
    list for a human; it does not claim to predict the new count.

    :param age_days: how long ago the record was collected.
    :param citations: the stored citation count, when known.
    :returns: the ordering weight, higher meaning more worth re-fetching.
    """
    return age_days * math.log10(1 + max(citations or 0, 0))


def rank_stale(
    records: list[Record], *, days: float = DEFAULT_STALE_DAYS, now: datetime | None = None
) -> list[Aged]:
    """Find the records older than ``days``, most worth re-fetching first.

    :param records: stored records.
    :param days: age from which a record counts as stale.
    :param now: the moment to measure against; defaults to the current UTC time.
    :returns: the stale records, ordered by :func:`_pressure` then by age.
    """
    moment = now or datetime.now(timezone.utc)
    aged = []
    for record in records:
        age = age_in_days(record, moment)
        if age is None or age < days:
            continue
        citations = record.get("cited_by_count")
        aged.append(
            Aged(
                record=record,
                age_days=age,
                pressure=_pressure(age, citations if isinstance(citations, int) else None),
                target=refresh_id(record),
            )
        )
    return sorted(aged, key=lambda item: (item.pressure, item.age_days), reverse=True)


def undated(records: list[Record]) -> int:
    """Count records that cannot be aged at all.

    :param records: stored records.
    :returns: how many carry no usable ``fetched_at``.
    """
    moment = datetime.now(timezone.utc)
    return sum(1 for record in records if age_in_days(record, moment) is None)


def render_staleness(
    records: list[Record], *, days: float = DEFAULT_STALE_DAYS, now: datetime | None = None, top: int = 10
) -> list[str]:
    """Report how current a collection is.

    :param records: stored records.
    :param days: age from which a record counts as stale.
    :param now: the moment to measure against; defaults to the current UTC time.
    :param top: how many of the most stale records to list.
    :returns: printable lines.
    """
    moment = now or datetime.now(timezone.utc)
    ages = [age for age in (age_in_days(record, moment) for record in records) if age is not None]
    if not ages:
        return [f"none of the {len(records)} records carries a collection timestamp"]
    stale = rank_stale(records, days=days, now=moment)
    missing = len(records) - len(ages)
    lines = [
        f"{len(records)} records collected between {max(ages):.0f} and {min(ages):.0f} days ago"
        + (f"; {missing} carry no timestamp" if missing else ""),
        f"{len(stale)} older than {days:g} days"
        + (f" ({len(stale) / len(records) * 100:.0f}% of the set)" if stale else ""),
    ]
    if not stale:
        return lines
    refreshable = [item for item in stale if item.target]
    lines.append(
        f"{len(refreshable)} of those can be re-listed by id, one page load each; "
        f"{len(stale) - len(refreshable)} would need their query re-run"
    )
    lines.extend(item.describe() for item in stale[:top])
    return lines


def refresh_ids(aged: list[Aged], *, limit: int = DEFAULT_REFRESH_LIMIT) -> list[str]:
    """Take the ids worth re-listing, in ranked order and without repeats.

    :param aged: ranked stale records.
    :param limit: how many ids to keep; each costs one page load.
    :returns: the numeric cluster ids.
    """
    ids: list[str] = []
    for item in aged:
        if item.target and item.target not in ids:
            ids.append(item.target)
        if len(ids) >= limit:
            break
    return ids


def render_refresh_list(aged: list[Aged], *, limit: int = DEFAULT_REFRESH_LIMIT) -> list[str]:
    """Build the refresh file: one id per line, each preceded by what it stands for.

    The format is the one ``scholar-crawler --clusters-file`` reads, so the output of a digest
    is the input of the next crawl.

    :param aged: ranked stale records.
    :param limit: how many ids to write.
    :returns: the file's lines, comments included.
    """
    kept = refresh_ids(aged, limit=limit)
    by_id = {item.target: item for item in reversed(aged) if item.target}
    lines = [
        "# stale records worth re-listing, most-moved first",
        "# feed this back with: scholar-crawler --clusters-file <this file> -p 1",
    ]
    for cluster_id in kept:
        item = by_id[cluster_id]
        title = " ".join(str(item.record.get("title") or "untitled").split())
        lines.append(f"# {item.age_days:.0f} days old: {clip(title, 80)}")
        lines.append(cluster_id)
    return lines
