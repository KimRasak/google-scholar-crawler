"""Citation-graph expansion: turning collected records into the next level of listings.

Expansion multiplies requests fast — one level of five listings at ten results each is
fifty more records — so every level is bounded by an explicit breadth, a citation floor
and a visited set that keeps the same work from being requested twice.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from .models import ScholarResult, SearchRequest
from .urls import parse_cluster_id


@dataclass(slots=True)
class FollowPolicy:
    """How far and how wide to walk the citation graph.

    :param depth: levels of citing works to expand; 0 crawls only the seed listings.
    :param breadth: listings started per level, taking the most-cited records first.
    :param min_citations: skip records cited fewer times than this.
    """

    depth: int = 0
    breadth: int = 5
    min_citations: int = 0

    def __post_init__(self) -> None:
        """Reject settings that would expand nothing or without bound.

        :raises ValueError: on a negative depth or floor, or a breadth below 1.
        """
        if self.depth < 0:
            raise ValueError("follow depth must not be negative")
        if self.breadth < 1:
            raise ValueError("follow breadth must be at least 1")
        if self.min_citations < 0:
            raise ValueError("citation floor must not be negative")

    @property
    def enabled(self) -> bool:
        """Whether any expansion level is requested."""
        return self.depth > 0

    def estimate(self, seeds: int) -> int:
        """Report the worst-case number of listings a run would request.

        :param seeds: number of seed listings.
        :returns: seed listings plus every level expanded to full breadth.
        """
        total = seeds
        level = seeds
        for _ in range(self.depth):
            level *= self.breadth
            total += level
        return total


def next_level(
    results: Iterable[ScholarResult],
    template: SearchRequest,
    policy: FollowPolicy,
    visited: set[str],
) -> list[SearchRequest]:
    """Select the records worth expanding and turn them into citing-works listings.

    Records are taken most-cited first, because a citation listing is only worth its
    requests when the cited work actually has citing works to page through.

    :param results: records collected at the current level.
    :param template: request whose filters (years, language, sorting) the new listings copy.
    :param policy: breadth and citation floor to apply.
    :param visited: cites ids already requested; updated in place.
    :returns: the listings to crawl next, at most ``policy.breadth`` of them.
    """
    candidates = [
        result
        for result in results
        if result.cited_by_url and (result.cited_by_count or 0) >= policy.min_citations
    ]
    candidates.sort(key=lambda result: result.cited_by_count or 0, reverse=True)
    requests: list[SearchRequest] = []
    for result in candidates:
        try:
            cites_id = parse_cluster_id(result.cited_by_url or "")
        except ValueError:  # a citing-works link Scholar rendered without an id
            continue
        if cites_id in visited:
            continue
        visited.add(cites_id)
        requests.append(replace(template, query="", cites=cites_id, cluster=None))
        if len(requests) >= policy.breadth:
            break
    return requests
