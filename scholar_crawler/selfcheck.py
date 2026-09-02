"""Self-check of the result-page parser against a live page.

Scholar changes its markup without notice, and a parser that silently returns empty
fields is worse than one that fails. These checks turn one fetched page into a
pass/fail report over the fields the rest of the tool depends on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import PageResult, ScholarResult


@dataclass(slots=True)
class Check:
    """One verified property of a fetched result page.

    :param name: short identifier printed in the report.
    :param ok: whether the property held.
    :param detail: measured value, shown whether the check passed or failed.
    """

    name: str
    ok: bool
    detail: str


def _fraction(results: list[ScholarResult], predicate: str) -> tuple[int, int]:
    """Count records whose named field is set.

    :param results: parsed records.
    :param predicate: attribute name to test for truthiness.
    :returns: matching count and total count.
    """
    return sum(1 for result in results if getattr(result, predicate)), len(results)


def check_page(page: PageResult) -> list[Check]:
    """Verify that a fetched result page still yields the fields the tool relies on.

    :param page: a parsed result page, normally the first page of a broad query.
    :returns: one check per verified property, in report order.
    """
    results = page.results
    titled, total = _fraction(results, "title")
    linkable = [result for result in results if not result.citation_only]
    linked = sum(1 for result in linkable if result.link)
    bylined, _ = _fraction(results, "byline")
    dated, _ = _fraction(results, "year")
    described, _ = _fraction(results, "snippet")
    carded, _ = _fraction(results, "cluster_id")
    cited = [result for result in results if result.cited_by_count and result.cited_by_url]
    return [
        Check("results_parsed", total >= 5, f"{total} records on the page"),
        Check("titles", total > 0 and titled == total, f"{titled}/{total} have a title"),
        Check(
            "links",
            bool(linkable) and linked == len(linkable),
            f"{linked}/{len(linkable)} non-citation records have a link",
        ),
        Check("bylines", total > 0 and bylined == total, f"{bylined}/{total} have an author line"),
        Check("years", total > 0 and dated >= total * 0.6, f"{dated}/{total} have a year"),
        Check("snippets", total > 0 and described >= total * 0.5, f"{described}/{total} have a snippet"),
        Check(
            "card_ids",
            total > 0 and carded == total,
            f"{carded}/{total} carry Scholar's data-cid (needed for BibTeX)",
        ),
        Check("citation_counts", bool(cited), f"{len(cited)}/{total} link their citing works"),
        Check(
            "total_estimate",
            page.total_estimate is not None,
            f"result count read as {page.total_estimate}",
        ),
        Check("pagination", page.has_next, f"next-page link {'found' if page.has_next else 'missing'}"),
    ]


def report(checks: list[Check]) -> bool:
    """Print the checks and report whether all of them passed.

    :param checks: results of :func:`check_page`.
    :returns: True when every check passed.
    """
    width = max(len(check.name) for check in checks)
    for check in checks:
        status = "ok  " if check.ok else "FAIL"
        print(f"[check] {check.name.ljust(width)}  {status}  {check.detail}", flush=True)
    failed = [check.name for check in checks if not check.ok]
    if failed:
        print(
            f"[check] {len(failed)} failed: {', '.join(failed)}. Scholar's markup may have "
            "changed; rerun with --dump-html to capture the page and adjust the selectors.",
            flush=True,
        )
        return False
    print(f"[check] all {len(checks)} checks passed", flush=True)
    return True
