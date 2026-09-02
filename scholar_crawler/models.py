"""Data records produced by the crawler."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SearchRequest:
    """One Google Scholar query, independent of pagination."""

    query: str
    year_low: int | None = None
    year_high: int | None = None
    language: str | None = None
    sort_by_date: bool = False
    include_citations: bool = True
    include_patents: bool = True
    review_only: bool = False

    def signature(self) -> str:
        """Stable key for resume state: identical requests share one cursor."""
        parts = [
            self.query,
            f"lo={self.year_low or ''}",
            f"hi={self.year_high or ''}",
            f"lang={self.language or ''}",
            f"date={int(self.sort_by_date)}",
            f"cit={int(self.include_citations)}",
            f"pat={int(self.include_patents)}",
            f"rev={int(self.review_only)}",
        ]
        return "|".join(parts)


@dataclass(slots=True)
class ScholarResult:
    """A single result card from a Google Scholar result page."""

    cluster_id: str | None
    position: int
    title: str
    link: str | None
    resource_link: str | None
    resource_type: str | None
    byline: str
    authors: str | None
    venue: str | None
    year: int | None
    snippet: str
    cited_by_count: int | None
    cited_by_url: str | None
    versions_count: int | None
    versions_url: str | None
    related_url: str | None
    citation_only: bool
    query: str = ""
    page_start: int = 0
    fetched_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping for JSONL/CSV writers."""
        return dataclasses.asdict(self)

    def dedup_key(self) -> str:
        """Identity used to drop repeats across pages and reruns."""
        return self.cluster_id or f"{self.title}::{self.link or ''}"


@dataclass(slots=True)
class PageResult:
    """Outcome of fetching one result page."""

    start: int
    results: list[ScholarResult]
    total_estimate: int | None
    has_next: bool
