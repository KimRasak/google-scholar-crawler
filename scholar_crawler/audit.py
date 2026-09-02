"""Auditing collected records for fields that parsed into something implausible.

Scholar's result cards carry one grey line holding authors, venue, year and site, and the
parser splits it by position. That works for the usual card and fails quietly on the rest:
a venue that is really a page range, a year that belongs to the journal title, an author
list Scholar itself truncated. Nothing downstream notices — ``--group-by year`` simply
groups a wrong year.

This module answers "how wrong is what I already collected" from local files only, so the
answer costs nothing and can be repeated on every batch.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

Record = dict[str, Any]

EARLIEST_YEAR = 1800
"""Scholar indexes older works, but a year below this is a parse failure, not a publication."""

PAGES_ONLY = re.compile(r"^[\d\s\-–—,()–:.]+$")
"""A venue made only of digits and separators is a volume, issue or page range."""

VOLUME_ISSUE = re.compile(r"^\d+\s*\(\d*\)")
"""``12(3)`` and ``12()`` are volume/issue prefixes that lost their journal name."""

HOSTNAME = re.compile(r"^[\w.-]+\.(com|org|edu|net|gov|io|cn|uk|de)$", re.IGNORECASE)
"""A venue that is a bare hostname means the site column landed in the venue field."""

YEAR_IN_TEXT = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
"""Years as they appear inside free text."""

TRUNCATION = ("…", "...")
"""Scholar itself elides long author lists; the record is lossy, not wrong."""


def _year(record: Record) -> int | None:
    """Read the stored year as an int.

    :param record: one collected record.
    :returns: the year, or None when absent or unreadable.
    """
    value = record.get("year")
    return value if isinstance(value, int) else None


def _text(record: Record, field: str) -> str:
    """Read a text field, treating any non-string as absent.

    :param record: one collected record.
    :param field: field name.
    :returns: the stripped value, or an empty string.
    """
    value = record.get(field)
    return value.strip() if isinstance(value, str) else ""


def _count(record: Record, field: str) -> int | None:
    """Read a count field as an int.

    :param record: one collected record.
    :param field: field name.
    :returns: the count, or None when absent or unreadable.
    """
    value = record.get(field)
    return value if isinstance(value, int) else None


def _year_out_of_range(record: Record) -> bool:
    year = _year(record)
    return year is not None and not EARLIEST_YEAR <= year <= datetime.now(timezone.utc).year + 1


def _year_disagrees_with_byline(record: Record) -> bool:
    year = _year(record)
    years = {int(found) for found in YEAR_IN_TEXT.findall(_text(record, "byline"))}
    return year is not None and bool(years) and year not in years


def _venue_looks_like_pages(record: Record) -> bool:
    venue = _text(record, "venue")
    return bool(venue) and (bool(PAGES_ONLY.match(venue)) or bool(VOLUME_ISSUE.match(venue)))


def _venue_keeps_year(record: Record) -> bool:
    return bool(YEAR_IN_TEXT.search(_text(record, "venue")))


def _venue_is_hostname(record: Record) -> bool:
    return bool(HOSTNAME.match(_text(record, "venue")))


def _authors_truncated(record: Record) -> bool:
    return _text(record, "authors").endswith(TRUNCATION)


def _citations_without_link(record: Record) -> bool:
    count = _count(record, "cited_by_count")
    return bool(count) and not _text(record, "cited_by_url")


def _negative_count(record: Record) -> bool:
    counts = (_count(record, "cited_by_count"), _count(record, "versions_count"))
    return any(count is not None and count < 0 for count in counts)


def _missing_cluster_id(record: Record) -> bool:
    return not _text(record, "cluster_id") and not record.get("citation_only")


@dataclass(slots=True, frozen=True)
class Check:
    """One thing that can be wrong with a record.

    :param name: short identifier printed in the report.
    :param severity: ``error`` for a value that is wrong, ``warn`` for missing or lossy.
    :param explain: what it means and what it breaks downstream.
    :param applies: True when this record has the problem.
    """

    name: str
    severity: str
    explain: str
    applies: Callable[[Record], bool]


CHECKS: tuple[Check, ...] = (
    Check(
        "title_missing",
        "error",
        "no title; the record is unusable",
        lambda record: not _text(record, "title"),
    ),
    Check(
        "title_tagged",
        "warn",
        "title still carries Scholar's [PDF]/[BOOK] tag",
        lambda record: _text(record, "title").startswith("["),
    ),
    Check(
        "year_out_of_range",
        "error",
        "year is not a plausible publication year, so year filters and grouping are wrong",
        _year_out_of_range,
    ),
    Check(
        "year_disagrees_with_byline",
        "error",
        "the stored year appears nowhere in the byline it was read from",
        _year_disagrees_with_byline,
    ),
    Check(
        "year_missing",
        "warn",
        "no year, so year filters and year grouping skip it",
        lambda record: _year(record) is None,
    ),
    Check(
        "venue_looks_like_pages",
        "error",
        "venue is a volume, issue or page range, so venue grouping is wrong",
        _venue_looks_like_pages,
    ),
    Check(
        "venue_keeps_year",
        "error",
        "venue still contains a year, which splits one venue into several groups",
        _venue_keeps_year,
    ),
    Check(
        "venue_is_hostname",
        "warn",
        "venue is a bare hostname: the site column landed in the venue field",
        _venue_is_hostname,
    ),
    Check(
        "venue_missing",
        "warn",
        "no venue, so venue grouping skips it",
        lambda record: not _text(record, "venue"),
    ),
    Check(
        "authors_missing",
        "warn",
        "no authors, so author grouping and BibTeX skip it",
        lambda record: not _text(record, "authors"),
    ),
    Check(
        "authors_truncated",
        "warn",
        "Scholar elided the author list, so BibTeX gets 'and others'",
        _authors_truncated,
    ),
    Check(
        "citations_without_link",
        "error",
        "a citation count with no citing-works link, so --follow-cites cannot expand it",
        _citations_without_link,
    ),
    Check("negative_count", "error", "a negative citation or version count", _negative_count),
    Check(
        "cluster_id_missing",
        "warn",
        "no card id, so BibTeX export and citation expansion cannot address this record",
        _missing_cluster_id,
    ),
)
"""Every check, ordered by the field it concerns."""


@dataclass(slots=True, frozen=True)
class Finding:
    """How many records tripped one check.

    :param check: the check that matched.
    :param count: how many records matched.
    :param total: records audited.
    :param examples: a few offending values, shortened for printing.
    """

    check: Check
    count: int
    total: int
    examples: tuple[str, ...]

    @property
    def share(self) -> float:
        """Fraction of audited records that tripped the check.

        :returns: the share between 0 and 1.
        """
        return self.count / self.total if self.total else 0.0

    def describe(self) -> str:
        """Format the finding as one line.

        :returns: severity, name, count, share and what it breaks.
        """
        return (
            f"{self.check.severity:5} {self.check.name:26} {self.count:5} "
            f"{self.share * 100:5.1f}%  {self.check.explain}"
        )


def _example(record: Record, check: Check) -> str:
    """Describe one offending record for the report.

    :param record: the record that tripped the check.
    :param check: the check it tripped.
    :returns: the offending value next to the record's title.
    """
    field = check.name.split("_")[0]
    value = record.get(field)
    shown = str(value) if value not in (None, "") else "<empty>"
    title = _text(record, "title") or "<untitled>"
    return f"{shown[:60]} | {title[:52]}"


def _ordered(findings: list[Finding]) -> list[Finding]:
    """Order findings for a report: errors first, then by how many records tripped them.

    :param findings: findings in check order.
    :returns: the same findings, sorted.
    """
    return sorted(findings, key=lambda finding: (finding.check.severity != "error", -finding.count))


def audit_records(records: list[Record], *, examples: int = 2) -> list[Finding]:
    """Run every check over the records.

    :param records: merged records to audit.
    :param examples: how many offending values to keep per finding.
    :returns: findings with at least one match, errors first, then by count.
    """
    findings = []
    for check in CHECKS:
        matched = [record for record in records if check.applies(record)]
        if not matched:
            continue
        findings.append(
            Finding(
                check=check,
                count=len(matched),
                total=len(records),
                examples=tuple(_example(record, check) for record in matched[:examples]),
            )
        )
    return _ordered(findings)


def render_audit(findings: list[Finding], total: int) -> list[str]:
    """Format the audit for the terminal.

    :param findings: findings to print.
    :param total: records audited.
    :returns: printable lines.
    """
    if not findings:
        return [f"audit of {total} records: nothing implausible found"]
    errors = sum(1 for finding in findings if finding.check.severity == "error")
    header = (
        f"audit of {total} records: {len(findings)} checks tripped "
        f"({errors} error{'' if errors == 1 else 's'}, {len(findings) - errors} warnings)"
    )
    lines = [header]
    for finding in findings:
        lines.append(f"  {finding.describe()}")
        for example in finding.examples:
            lines.append(f"      e.g. {example}")
    return lines


ALARM_SHARE = 0.2
"""An error check tripping this often is a layout change, not an odd record."""

ALARM_RECORDS = 3
"""Below this many matches, a share means nothing: two odd records out of three are normal."""


@dataclass(slots=True)
class AuditTally:
    """Audits records one at a time, as a run writes them.

    A run cannot re-read what it wrote — the file may hold earlier runs — and keeping every
    record in memory to audit at the end would grow without bound. Counting per check as
    records go past costs one pass and a handful of strings.

    :param keep: examples to remember per check.
    :param total: records observed.
    """

    keep: int = 2
    total: int = 0
    _counts: dict[str, int] = field(default_factory=dict)
    _examples: dict[str, list[str]] = field(default_factory=dict)

    def observe(self, record: Record) -> None:
        """Run every check against one record.

        :param record: the record just written.
        """
        self.total += 1
        for check in CHECKS:
            if not check.applies(record):
                continue
            self._counts[check.name] = self._counts.get(check.name, 0) + 1
            examples = self._examples.setdefault(check.name, [])
            if len(examples) < self.keep:
                examples.append(_example(record, check))

    def findings(self) -> list[Finding]:
        """Summarize what the observed records tripped.

        :returns: findings ordered as :func:`audit_records` orders them.
        """
        return _ordered(
            [
                Finding(
                    check=check,
                    count=self._counts[check.name],
                    total=self.total,
                    examples=tuple(self._examples.get(check.name, ())),
                )
                for check in CHECKS
                if self._counts.get(check.name)
            ]
        )

    def alarms(self, *, share: float = ALARM_SHARE, minimum: int = ALARM_RECORDS) -> list[Finding]:
        """Findings serious and widespread enough to interrupt a run's summary.

        :param share: minimum fraction of records tripping the check.
        :param minimum: minimum number of records tripping the check.
        :returns: the error-severity findings over both thresholds.
        """
        return [
            finding
            for finding in self.findings()
            if finding.check.severity == "error"
            and finding.count >= minimum
            and finding.share >= share
        ]

    def describe_alarms(self, *, share: float = ALARM_SHARE, minimum: int = ALARM_RECORDS) -> list[str]:
        """Format the alarms as lines a run prints after its summary.

        :param share: minimum fraction of records tripping the check.
        :param minimum: minimum number of records tripping the check.
        :returns: printable lines; empty when nothing crossed the thresholds.
        """
        alarms = self.alarms(share=share, minimum=minimum)
        if not alarms:
            return []
        lines = [
            f"{len(alarms)} field(s) parsed badly for a large share of this run's records — "
            "Scholar's layout may have changed"
        ]
        for finding in alarms:
            lines.append(
                f"  {finding.check.name}: {finding.count} of {self.total} records "
                f"({finding.share * 100:.0f}%) — {finding.check.explain}"
            )
            for example in finding.examples:
                lines.append(f"      e.g. {example}")
        lines.append("run --self-check to test the parser, or scholar-digest --audit for the details")
        return lines
