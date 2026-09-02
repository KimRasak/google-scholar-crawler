"""HTML parsing of Google Scholar result pages and author profiles.

Selectors follow the ``gs_*`` and ``gsc_*`` class names Scholar has used for years.
Counts are read from link hrefs (``cites=``, ``cluster=``, ``related:``) and from
fixed table positions rather than from label text, so parsing does not depend on
the interface language.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup, NavigableString, Tag

from .models import AuthorPage, AuthorProfile, PageResult, ScholarResult
from .urls import absolute

_INT_RE = re.compile(r"\d[\d,\.\s\u00a0]*")
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")
_START_RE = re.compile(r"[?&]start=(\d+)")
_BIBTEX_KEY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
_TAG_PREFIX_RE = re.compile(r"^\s*\[[^\]]{1,12}\]\s*")
_WS_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,;.])")


def _text(node: Tag | None) -> str:
    """Return ``node``'s rendered text with whitespace runs collapsed.

    Child elements are concatenated without an added separator, matching how a
    browser renders inline markup: Scholar bolds query terms mid-word
    (``multi-<b>agents</b>``), and inserting separators would break those words.
    Whitespace left in front of ``,;.`` by line-wrapped markup is dropped, so a
    linked organization inside an affiliation reads ``University of X, Mila``.

    :param node: element to read, or None.
    :returns: normalized text; empty when ``node`` is None.
    """
    if node is None:
        return ""
    collapsed = _WS_RE.sub(" ", node.get_text(""))
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", collapsed).strip()


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


def bibtex_link(html: str) -> str | None:
    """Find the BibTeX export link in a cite-popup fragment.

    The link is matched by its ``scholar.bib`` path or its ``scisf=4`` format code, not
    by its label, so the interface language and the order of the export links do not
    matter.

    :param html: cite-popup HTML.
    :returns: the export href as found in the page, or None when absent.
    """
    soup = BeautifulSoup(html, "lxml")
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href"))
        if "scholar.bib" in href or "scisf=4" in href:
            return href
    return None


def parse_bibtex(body: str) -> str | None:
    """Extract a BibTeX entry from an export response.

    Chrome renders the ``text/plain`` export inside a ``<pre>`` element; a raw body is
    accepted as well.

    :param body: response body, either the rendered HTML or the raw export.
    :returns: the entry starting at ``@``, or None when the body holds no entry.
    """
    text = body
    if "<pre" in body.lower() or "<html" in body.lower():
        block = BeautifulSoup(body, "lxml").select_one("pre")
        text = block.get_text("") if block is not None else ""
    start = text.find("@")
    if start < 0 or "{" not in text[start:]:
        return None
    return text[start:].strip()


def bibtex_key(entry: str) -> str | None:
    """Read the citation key of a BibTeX entry.

    :param entry: a BibTeX entry.
    :returns: the key, or None when the entry has no ``@type{key,`` header.
    """
    match = _BIBTEX_KEY_RE.search(entry)
    return match.group(1) if match else None


def _profile_stats(soup: BeautifulSoup) -> list[tuple[int | None, int | None]]:
    """Read the ``#gsc_rsb_st`` summary table as (all-time, recent) pairs.

    Rows are Citations, h-index and i10-index in that fixed order; the language of
    the labels is irrelevant.

    :param soup: parsed profile page.
    :returns: one pair per data row, missing cells as None.
    """
    pairs: list[tuple[int | None, int | None]] = []
    for row in soup.select("#gsc_rsb_st tbody tr"):
        cells = [_first_int(_text(cell)) for cell in row.select("td.gsc_rsb_std")]
        pairs.append((cells[0] if cells else None, cells[1] if len(cells) > 1 else None))
    return pairs


def _profile_header(soup: BeautifulSoup, user_id: str, fetched_at: str) -> AuthorProfile:
    """Read identity and citation summary from a profile page header.

    The header holds three unlabelled ``div.gsc_prf_il`` lines: the affiliation
    (which may link an organization), the verified-email line (which may link the
    author's homepage) and the interest list. They are told apart by their ids and
    position, not by text, so the interface language does not matter.

    :param soup: parsed profile page.
    :param user_id: profile id, recorded on the result.
    :param fetched_at: ISO timestamp recorded on the result.
    :returns: the profile record; unavailable fields are None or empty.
    """
    affiliation_line = next(
        (line for line in soup.select("div.gsc_prf_il") if not line.get("id")), None
    )
    organization = affiliation_line.select_one("a.gsc_prf_ila") if affiliation_line else None
    email_line = soup.select_one("#gsc_prf_ivh")
    homepage = None
    verified_email = None
    if email_line is not None:
        homepage = next(
            (
                anchor.get("href")
                for anchor in email_line.select("a[href]")
                if str(anchor.get("href")).startswith(("http://", "https://"))
                and "scholar.google." not in str(anchor.get("href"))
            ),
            None,
        )
        direct = "".join(str(child) for child in email_line.children if isinstance(child, NavigableString))
        verified_email = _WS_RE.sub(" ", direct).strip(" -\u2013\t\n") or None
    stats = _profile_stats(soup)
    citations, h_index, i10_index = (stats + [(None, None)] * 3)[:3]
    return AuthorProfile(
        user_id=user_id,
        name=_text(soup.select_one("#gsc_prf_in")),
        affiliation=_text(affiliation_line) or None,
        organization=_text(organization) or None,
        homepage=homepage,
        verified_email=verified_email,
        interests=[_text(link) for link in soup.select("#gsc_prf_int a")],
        cited_by_total=citations[0],
        cited_by_recent=citations[1],
        h_index=h_index[0],
        h_index_recent=h_index[1],
        i10_index=i10_index[0],
        i10_index_recent=i10_index[1],
        fetched_at=fetched_at,
    )


def parse_author_page(html: str, *, user_id: str, cstart: int = 0) -> AuthorPage:
    """Parse one batch of an author profile into a profile record and publications.

    :param html: full profile-page HTML.
    :param user_id: profile id, recorded on every produced record.
    :param cstart: publication offset of this batch, used for positions and provenance.
    :returns: the profile header, its publications, and whether more remain.
    """
    soup = BeautifulSoup(html, "lxml")
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    profile = _profile_header(soup, user_id, fetched_at)
    results: list[ScholarResult] = []

    for index, row in enumerate(soup.select("tr.gsc_a_tr")):
        title_link = row.select_one("a.gsc_a_at")
        if title_link is None:
            continue
        grays = row.select("div.gs_gray")
        authors = _text(grays[0]) if grays else None
        venue = _text(grays[1]) if len(grays) > 1 else None
        cites_link = row.select_one("a.gsc_a_ac")
        cited_count = None
        if cites_link is not None:
            cited_count = _first_int(_text(cites_link)) or 0
        href = title_link.get("href") or ""
        citation_id = href.split("citation_for_view=")[-1] if "citation_for_view=" in href else None
        results.append(
            ScholarResult(
                cluster_id=None,
                position=cstart + index + 1,
                title=_text(title_link),
                link=absolute(href),
                resource_link=None,
                resource_type=None,
                byline=" - ".join(part for part in (authors, venue) if part),
                authors=authors,
                venue=venue,
                year=_first_int(_text(row.select_one("span.gsc_a_h"))),
                snippet="",
                cited_by_count=cited_count,
                cited_by_url=absolute(cites_link.get("href")) if cites_link else None,
                versions_count=None,
                versions_url=None,
                related_url=None,
                citation_only=False,
                query=f"author:{user_id}",
                page_start=cstart,
                fetched_at=fetched_at,
                extra={"citation_id": citation_id},
            )
        )

    more_button = soup.select_one("#gsc_bpf_more")
    has_more = more_button is not None and not more_button.has_attr("disabled")
    return AuthorPage(cstart=cstart, profile=profile, results=results, has_more=has_more)
