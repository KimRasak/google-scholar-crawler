"""Run planning: what a crawl would request, and roughly how long it would take.

Costs multiply quickly — pages times listings times BibTeX loads — and the multiplication
happens in the operator's head. The plan does that arithmetic before anything is
requested, so an expensive run can be reconsidered instead of discovered halfway through.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .crawler import Pacing, delay_span
from .expand import FollowPolicy
from .models import AuthorRequest, SearchRequest
from .urls import AUTHOR_PAGE_SIZE, RESULTS_PER_PAGE, author_url, search_url

LOAD_SECONDS = 3.0
"""Rough per-load cost beyond the pacing delay: navigation, parsing and the human-like dwell."""


def pages_needed(pages: int, max_results: int | None, per_page: int) -> int:
    """Report how many pages a listing actually needs.

    :param pages: page budget from ``--pages``.
    :param max_results: record cap from ``--max-results``, or None.
    :param per_page: records Scholar returns per page.
    :returns: the smaller of the page budget and the pages the record cap requires.
    """
    if max_results is None:
        return pages
    return max(1, min(pages, math.ceil(max_results / per_page)))


@dataclass(slots=True)
class RunPlan:
    """The worst-case cost of a planned run.

    :param targets: human-readable label and first URL of every seed target.
    :param seed_loads: page loads for the seed listings and profiles.
    :param seed_records: records those loads can yield.
    :param follow_listings: citation listings the expansion could add.
    :param follow_loads: page loads those listings would cost.
    :param follow_records: records the expansion could yield.
    :param bibtex_loads: extra loads for BibTeX export.
    :param seconds: estimated wall-clock duration.
    :param cooldowns: long pauses the pacing would insert.
    :param pacing: the rhythm the estimate assumes.
    """

    targets: list[tuple[str, str]]
    seed_loads: int
    seed_records: int
    follow_listings: int
    follow_loads: int
    follow_records: int
    bibtex_loads: int
    seconds: float
    cooldowns: int
    pacing: Pacing

    @property
    def total_loads(self) -> int:
        """Total page loads the run could issue."""
        return self.seed_loads + self.follow_loads + self.bibtex_loads

    @property
    def total_records(self) -> int:
        """Records the run could collect, before deduplication."""
        return self.seed_records + self.follow_records

    def render(self) -> list[str]:
        """Format the plan as printable lines.

        :returns: one line per target, then the load and duration estimates.
        """
        lines = [f"{label} -> {url}" for label, url in self.targets]
        lines.append(f"seed targets: {self.seed_loads} page loads, up to {self.seed_records} records")
        if self.follow_listings:
            lines.append(
                f"citation expansion: up to {self.follow_listings} listings, "
                f"{self.follow_loads} page loads, up to {self.follow_records} records"
            )
        if self.bibtex_loads:
            lines.append(f"bibtex export: up to {self.bibtex_loads} page loads")
        lines.append(f"total: up to {self.total_loads} page loads for {self.total_records} records")
        if self.seconds >= 5400:
            duration = f"{self.seconds / 3600:.1f} h"
        elif self.seconds >= 90:
            duration = f"{self.seconds / 60:.0f} min"
        else:
            duration = f"{self.seconds:.0f}s"
        detail = f"{delay_span(self.pacing.min_delay, self.pacing.max_delay)} between requests"
        if self.cooldowns:
            detail += f" plus {self.cooldowns} cooldowns of {self.pacing.cooldown_seconds:.0f}s"
        lines.append(f"estimated {duration} at {detail}")
        return lines


def plan_run(
    listings: list[SearchRequest],
    authors: list[AuthorRequest],
    *,
    pages: int,
    max_results: int | None,
    follow: FollowPolicy,
    bibtex: bool,
    pacing: Pacing,
    host: str,
) -> RunPlan:
    """Work out the worst-case cost of a run without requesting anything.

    Every number is an upper bound: Scholar stops earlier when a listing runs out of
    results, and the expansion stops when nothing is left worth expanding.

    :param listings: seed keyword, citation and version listings.
    :param authors: seed author profiles.
    :param pages: page budget per target.
    :param max_results: record cap per target, or None.
    :param follow: citation-graph expansion policy.
    :param bibtex: whether BibTeX export is enabled.
    :param pacing: the rhythm used for the duration estimate.
    :param host: Scholar host, used to render the first URL of each target.
    :returns: the planned cost.
    """
    listing_pages = pages_needed(pages, max_results, RESULTS_PER_PAGE)
    author_pages = pages_needed(pages, max_results, AUTHOR_PAGE_SIZE)
    listing_records = min(max_results or listing_pages * RESULTS_PER_PAGE, listing_pages * RESULTS_PER_PAGE)
    author_records = min(max_results or author_pages * AUTHOR_PAGE_SIZE, author_pages * AUTHOR_PAGE_SIZE)

    targets = [(request.label, search_url(request, host=host)) for request in listings]
    targets += [(f"author:{request.user_id}", author_url(request, 0, host=host)) for request in authors]

    seed_loads = len(listings) * listing_pages + len(authors) * author_pages
    seed_records = len(listings) * listing_records + len(authors) * author_records

    follow_listings = follow.estimate(len(listings) + len(authors)) - len(listings) - len(authors)
    follow_loads = follow_listings * listing_pages
    follow_records = follow_listings * listing_records

    # A profile publication needs its card id resolved first, so it costs one load more.
    bibtex_loads = 0
    if bibtex:
        bibtex_loads = (len(listings) * listing_records + follow_records) * 2
        bibtex_loads += len(authors) * author_records * 3

    total = seed_loads + follow_loads + bibtex_loads
    delay = (pacing.min_delay + pacing.max_delay) / 2
    cooldowns = total // pacing.cooldown_every if pacing.cooldown_every else 0
    seconds = max(0, total - 1) * delay + total * LOAD_SECONDS + cooldowns * pacing.cooldown_seconds
    return RunPlan(
        targets=targets,
        seed_loads=seed_loads,
        seed_records=seed_records,
        follow_listings=follow_listings,
        follow_loads=follow_loads,
        follow_records=follow_records,
        bibtex_loads=bibtex_loads,
        seconds=seconds,
        cooldowns=cooldowns,
        pacing=pacing,
    )
