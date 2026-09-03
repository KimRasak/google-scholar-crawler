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

    def describe(self) -> str:
        """Name this target the way output read outside its own run names it.

        :meth:`label` is enough while the command line is on screen next to it. A resume file
        or a takeover log is read later, where two runs of the same query differ only by their
        filters, so those are spelled out here.

        :returns: the label followed by the filters that distinguish this request.
        """
        years = f"{self.year_low or ''}-{self.year_high or ''}".strip("-")
        return self.label + _bracket(
            [
                self.language or "",
                years,
                "by date" if self.sort_by_date else "",
                "no citations" if not self.include_citations else "",
                "no patents" if not self.include_patents else "",
                "reviews only" if self.review_only else "",
            ]
        )

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


SEARCH_FIELDS = 9
"""``key=value`` fields a search signature carries after the query, which may contain anything."""


def parse_signature(signature: str) -> SearchRequest | AuthorRequest | None:
    """Rebuild the request a stored signature stands for.

    A signature is written for stability, not for reading, and a query may contain the ``|`` the
    fields are joined with — so the fixed number of trailing fields is split off from the right
    and everything before them is the query, whatever it holds.

    :param signature: a signature from :meth:`SearchRequest.signature` or
        :meth:`AuthorRequest.signature`.
    :returns: the request it stands for, or None when it follows neither layout.
    """
    if signature.startswith("author="):
        fields = signature.split("|")
        values = dict(part.split("=", 1) for part in fields if "=" in part)
        if len(fields) != 3:
            return None
        return AuthorRequest(
            user_id=values["author"],
            language=values.get("lang") or None,
            sort_by_year=values.get("year") == "1",
        )
    parts = signature.rsplit("|", SEARCH_FIELDS)
    if len(parts) != SEARCH_FIELDS + 1 or not all("=" in part for part in parts[1:]):
        return None
    values = dict(part.split("=", 1) for part in parts[1:])
    if set(values) != {"cites", "cluster", "lo", "hi", "lang", "date", "cit", "pat", "rev"}:
        return None
    try:
        return SearchRequest(
            query=parts[0],
            cites=values["cites"] or None,
            cluster=values["cluster"] or None,
            year_low=int(values["lo"]) if values["lo"] else None,
            year_high=int(values["hi"]) if values["hi"] else None,
            language=values["lang"] or None,
            sort_by_date=values["date"] == "1",
            include_citations=values["cit"] == "1",
            include_patents=values["pat"] == "1",
            review_only=values["rev"] == "1",
        )
    except ValueError:  # a signature with no entry point, or a year that is not a number
        return None


def describe_signature(signature: str) -> str:
    """Render a stored resume signature back into something readable.

    :param signature: a signature from :meth:`SearchRequest.signature` or
        :meth:`AuthorRequest.signature`.
    :returns: what :meth:`SearchRequest.describe` would say about the request it stands for, or
        the raw signature when it follows neither layout.
    """
    request = parse_signature(signature)
    return signature if request is None else request.describe()


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


def record_key(record: dict[str, Any]) -> str:
    """Identify a stored record the way :meth:`ScholarResult.dedup_key` identifies a live one.

    Records read back from JSONL are plain mappings, and every offline tool that groups them —
    merging, graph building — must agree with the crawler's sink on what one work is.

    :param record: a stored record.
    :returns: Scholar's card id when present, otherwise title and link.
    """
    return record.get("cluster_id") or f"{record.get('title')}::{record.get('link') or ''}"


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

    def describe(self) -> str:
        """Name this profile the way output read outside its own run names it.

        :returns: the label followed by the filters that distinguish this request.
        """
        return self.label + _bracket([self.language or "", "by year" if self.sort_by_year else ""])

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
