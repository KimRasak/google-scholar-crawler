"""HTML parsing of Google Scholar result pages.

Selectors follow the ``gs_*`` class names Scholar has used for years. Counts are
read from link hrefs (``cites=``, ``cluster=``, ``related:``) rather than from
link text, so parsing does not depend on the interface language.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup, Tag

from .models import PageResult, ScholarResult
from .urls import absolute

_INT_RE = re.compile(r"\d[\d,\.\s\u00a0]*")
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_START_RE = re.compile(r"[?&]start=(\d+)")
_TAG_PREFIX_RE = re.compile(r"^\s*\[[^\]]{1,12}\]\s*")
_WS_RE = re.compile(r"\s+")


def _text(node: Tag | None) -> str:
    """Return ``node``'s rendered text with whitespace runs collapsed.

    Child elements are concatenated without an added separator, matching how a
    browser renders inline markup: Scholar bolds query terms mid-word
    (``multi-<b>agents</b>``), and inserting separators would break those words.

    :param node: element to read, or None.
    :returns: normalized text; empty when ``node`` is None.
    """
    return _WS_RE.sub(" ", node.get_text("")).strip() if node is not None else ""


def _first_int(text: str | None) -> int | None:
    """Extract the first integer from ``text``, ignoring digit grouping.

    :param text: arbitrary label text.
    :returns: the integer, or None when ``text`` holds no digits.
    """
    if not text:
        return None
    match = _INT_RE.search(text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(0))
    return int(digits) if digits else None


def _all_ints(text: str) -> list[int]:
    """Extract every integer in ``text``, ignoring digit grouping.

    The result-count banner reads ``Page 2 of about 16,800 results`` from page two
    onward, so callers take the maximum rather than the first number.

    :param text: banner text.
    :returns: the integers in order of appearance.
    """
    values: list[int] = []
    for match in _INT_RE.finditer(text):
        digits = re.sub(r"[^\d]", "", match.group(0))
        if digits:
            values.append(int(digits))
    return values


def _split_byline(byline: str) -> tuple[str | None, str | None, int | None]:
    """Split a ``div.gs_a`` byline into authors, venue and year.

    :param byline: raw byline text, e.g. ``A Smith, B Lee - Nature, 2019 - nature.com``.
    :returns: authors, venue (with the year removed), and the publication year.
    """
    if not byline:
        return None, None, None
    segments = [segment.strip() for segment in byline.split(" - ")]
    authors = segments[0] or None
    middle = segments[1] if len(segments) > 1 else ""
    year_match = _YEAR_RE.search(middle) or _YEAR_RE.search(byline)
    year = int(year_match.group(1)) if year_match else None
    venue = re.sub(r",?\s*\b(1[89]\d{2}|20\d{2})\b", "", middle).strip(" ,") or None
    return authors, venue, year


def _footer_links(card: Tag) -> tuple[int | None, str | None, int | None, str | None, str | None]:
    """Read the citation, versions and related links from a result card footer.

    :param card: the ``div.gs_r`` element.
    :returns: cited-by count and URL, versions count and URL, and the related-articles URL.
    """
    cited_count = cited_url = versions_count = versions_url = related_url = None
    for anchor in card.select("div.gs_fl a[href]"):
        href = anchor.get("href") or ""
        text = anchor.get_text(" ", strip=True)
        if "cites=" in href and "cluster=" not in href:
            cited_count = _first_int(text)
            cited_url = absolute(href)
        elif "cluster=" in href:
            versions_count = _first_int(text)
            versions_url = absolute(href)
        elif "related:" in href:
            related_url = absolute(href)
    return cited_count, cited_url, versions_count, versions_url, related_url


def _resource_link(card: Tag) -> tuple[str | None, str | None]:
    """Read the side link to a full-text resource, when Scholar offers one.

    :param card: the ``div.gs_r`` element.
    :returns: the resource URL and its label (``PDF``, ``HTML``, ...), or (None, None).
    """
    anchor = card.select_one("div.gs_or_ggsm a[href], div.gs_ggsd a[href]")
    if anchor is None:
        return None, None
    label = anchor.select_one("span.gs_ctg2")
    kind = label.get_text(strip=True).strip("[]") if label else None
    return absolute(anchor.get("href")), kind


def parse_result_page(html: str, *, query: str = "", start: int = 0) -> PageResult:
    """Parse a Scholar result page into structured records.

    :param html: full page HTML.
    :param query: query string recorded on each result for provenance.
    :param start: result offset of this page, recorded on each result.
    :returns: the page's results plus the total estimate and next-page flag.
    """
    soup = BeautifulSoup(html, "lxml")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[ScholarResult] = []

    for index, card in enumerate(soup.select("div.gs_r.gs_or.gs_scl")):
        heading = card.select_one("h3.gs_rt")
        if heading is None:
            continue
        anchor = heading.select_one("a[href]")
        title = _TAG_PREFIX_RE.sub("", _text(anchor) if anchor else _text(heading))
        byline = _text(card.select_one("div.gs_a"))
        authors, venue, year = _split_byline(byline)
        snippet_tag = card.select_one("div.gs_rs")
        cited_count, cited_url, versions_count, versions_url, related_url = _footer_links(card)
        resource_link, resource_type = _resource_link(card)
        results.append(
            ScholarResult(
                cluster_id=card.get("data-cid"),
                position=start + index + 1,
                title=title,
                link=absolute(anchor.get("href")) if anchor else None,
                resource_link=resource_link,
                resource_type=resource_type,
                byline=byline,
                authors=authors,
                venue=venue,
                year=year,
                snippet=_text(snippet_tag),
                cited_by_count=cited_count,
                cited_by_url=cited_url,
                versions_count=versions_count,
                versions_url=versions_url,
                related_url=related_url,
                citation_only=anchor is None,
                query=query,
                page_start=start,
                fetched_at=fetched_at,
            )
        )

    counts = [
        value
        for banner in soup.select("div.gs_ab_mdw")
        for value in _all_ints(_text(banner))
    ]
    total_estimate = max(counts) if counts else None
    has_next = any(
        int(match.group(1)) > start
        for anchor in soup.select("a[href]")
        if (match := _START_RE.search(anchor.get("href") or ""))
    )
    return PageResult(start=start, results=results, total_estimate=total_estimate, has_next=has_next)
