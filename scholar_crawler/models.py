"""Data records produced by the crawler."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SearchRequest:
    """One Google Scholar listing to page through, independent of pagination.

    Exactly one entry point is required: a keyword ``query``, or ``cites`` to list
    the works citing a cluster, or ``cluster`` to list the versions of one work.
    A ``query`` may be combined with ``cites`` or ``cluster`` to search within it.
    """

    query: str = ""
    cites: str | None = None
    cluster: str | None = None
    year_low: int | None = None
    year_high: int | None = None
    language: str | None = None
    sort_by_date: bool = False
    include_citations: bool = True
    include_patents: bool = True
    review_only: bool = False

    def __post_init__(self) -> None:
        """Reject a request with no entry point instead of fetching Scholar's home page.

        :raises ValueError: when ``query``, ``cites`` and ``cluster`` are all empty.
        """
        if not self.query and not self.cites and not self.cluster:
            raise ValueError("a SearchRequest needs a query, a cites id or a cluster id")

    @property
    def label(self) -> str:
        """Short human-readable name for logs and progress output."""
        if self.query:
            return self.query
        return f"cites:{self.cites}" if self.cites else f"cluster:{self.cluster}"

    def signature(self) -> str:
        """Stable key for resume state: identical requests share one cursor."""
        parts = [
            self.query,
            f"cites={self.cites or ''}",
            f"cluster={self.cluster or ''}",
            f"lo={self.year_low or ''}",
            f"hi={self.year_high or ''}",
            f"lang={self.language or ''}",
            f"date={int(self.sort_by_date)}",
            f"cit={int(self.include_citations)}",
            f"pat={int(self.include_patents)}",
            f"rev={int(self.review_only)}",
        ]
        return "|".join(parts)


def describe_signature(signature: str) -> str:
    """Render a stored resume signature back into something readable.

    Signatures are built for stability, not for reading; this turns one back into the
    target it stands for so stored progress can be reviewed.

    :param signature: a signature from :meth:`SearchRequest.signature` or
        :meth:`AuthorRequest.signature`.
    :returns: the target followed by the filters that distinguish it, or the raw
        signature when it does not follow either layout.
    """
    fields = signature.split("|")
    values = dict(
        part.split("=", 1) for part in fields if "=" in part and not part.startswith("author=")
    )
    if fields[0].startswith("author="):
        who = fields[0].removeprefix("author=")
        extras = [values.get("lang") or "", "by year" if values.get("year") == "1" else ""]
        return f"author:{who}" + _bracket(extras)
    if len(fields) < 10:
        return signature
    target = fields[0] or (
        f"cites:{values['cites']}" if values.get("cites") else f"cluster:{values.get('cluster', '')}"
    )
    years = f"{values.get('lo') or ''}-{values.get('hi') or ''}".strip("-")
    extras = [
        values.get("lang") or "",
        years,
        "by date" if values.get("date") == "1" else "",
        "no citations" if values.get("cit") == "0" else "",
        "no patents" if values.get("pat") == "0" else "",
        "reviews only" if values.get("rev") == "1" else "",
    ]
    return f"{target}{_bracket(extras)}"


def _bracket(extras: list[str]) -> str:
    """Join the non-empty extras into a bracketed suffix.

    :param extras: descriptions, some of which may be empty.
    :returns: ``" [a, b]"``, or an empty string when nothing is left.
    """
    kept = [extra for extra in extras if extra]
    return f" [{', '.join(kept)}]" if kept else ""


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
    """Outcome of fetching one result page.

    :param start: result offset this page began at.
    :param results: parsed result cards, in Scholar order.
    :param total_estimate: Scholar's estimated hit count for the whole query.
    :param has_next: True when Scholar offers a further page.
    :param truncated: True when the record cap cut this page short, which means the run
        stopped here by choice while Scholar still had results to give.
    """

    start: int
    results: list[ScholarResult]
    total_estimate: int | None
    has_next: bool
    truncated: bool = False


@dataclass(slots=True)
class AuthorRequest:
    """One Google Scholar author profile to page through.

    :param user_id: the ``user=`` id of the profile.
    :param language: interface language (``hl``).
    :param sort_by_year: list newest publications first instead of most cited.
    """

    user_id: str
    language: str | None = None
    sort_by_year: bool = False

    @property
    def label(self) -> str:
        """Short human-readable name for logs and progress output."""
        return f"author:{self.user_id}"

    def signature(self) -> str:
        """Stable key for resume state: identical requests share one cursor."""
        return f"author={self.user_id}|lang={self.language or ''}|year={int(self.sort_by_year)}"


@dataclass(slots=True)
class AuthorProfile:
    """Header metadata of an author profile: identity plus citation summary."""

    user_id: str
    name: str
    affiliation: str | None
    organization: str | None
    homepage: str | None
    verified_email: str | None
    interests: list[str]
    cited_by_total: int | None
    cited_by_recent: int | None
    h_index: int | None
    h_index_recent: int | None
    i10_index: int | None
    i10_index_recent: int | None
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable mapping for the profile writer."""
        return dataclasses.asdict(self)

    def dedup_key(self) -> str:
        """Identity used to keep one record per profile."""
        return self.user_id


@dataclass(slots=True)
class AuthorPage:
    """Outcome of fetching one batch of an author's publication list.

    Publications are returned as :class:`ScholarResult` so author output shares the
    JSONL schema, dedup and CSV export of keyword and citation-list crawls.
    """

    cstart: int
    profile: AuthorProfile
    results: list[ScholarResult]
    has_more: bool
    truncated: bool = False
    """True when the record cap cut this batch short, so the profile is not finished."""
