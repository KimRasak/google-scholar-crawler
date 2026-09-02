"""Google Scholar URL construction."""

from __future__ import annotations

from urllib.parse import urlencode

from .models import SearchRequest

SCHOLAR_HOST = "https://scholar.google.com"
RESULTS_PER_PAGE = 10


def search_url(request: SearchRequest, start: int = 0, host: str = SCHOLAR_HOST) -> str:
    """Build the result-page URL for ``request`` at result offset ``start``.

    :param request: the query and its filters.
    :param start: zero-based result offset; Google Scholar pages in steps of 10.
    :param host: Scholar host to hit, e.g. a regional mirror.
    :returns: absolute URL of the result page.
    """
    params: dict[str, str | int] = {"q": request.query, "hl": request.language or "en"}
    if start:
        params["start"] = start
    if request.year_low is not None:
        params["as_ylo"] = request.year_low
    if request.year_high is not None:
        params["as_yhi"] = request.year_high
    if request.sort_by_date:
        params["scisbd"] = 1
    if request.review_only:
        params["as_rr"] = 1
    params["as_vis"] = 0 if request.include_citations else 1
    params["as_sdt"] = "0,5" if request.include_patents else "0"
    return f"{host}/scholar?{urlencode(params)}"


def absolute(href: str | None, host: str = SCHOLAR_HOST) -> str | None:
    """Resolve a Scholar-relative href against ``host``.

    :param href: href as found in the page, possibly relative or missing.
    :param host: Scholar host used for relative hrefs.
    :returns: absolute URL, or None when ``href`` is empty.
    """
    if not href:
        return None
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return f"https:{href}"
    if not href.startswith("/"):
        href = f"/{href}"
    return f"{host}{href}"
