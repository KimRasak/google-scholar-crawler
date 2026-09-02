"""Google Scholar URL construction."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import AuthorRequest, SearchRequest

SCHOLAR_HOST = "https://scholar.google.com"
READABLE_PARAMS = frozenset(
    {"q", "start", "cites", "cluster", "user", "hl", "as_sdt", "as_ylo", "as_yhi", "cstart", "pagesize"}
)
"""Query parameters safe to keep when a URL is written to a log: they describe the request."""

CHALLENGE_PATH = "/sorry"
"""On the challenge path ``q`` is the challenge token, not a search query, so it is redacted too."""
RESULTS_PER_PAGE = 10
AUTHOR_PAGE_SIZE = 100
"""Largest publication batch a profile page serves in one request."""

_ID_RE = re.compile(r"(?:cites|cluster)=(\d+)")
_DIGITS_RE = re.compile(r"^\d+$")
_USER_RE = re.compile(r"user=([\w-]{12})")
_USER_ID_RE = re.compile(r"^[\w-]{12}$")


def parse_cluster_id(value: str) -> str:
    """Accept a bare Scholar id or any URL carrying ``cites=``/``cluster=``.

    Lets the ``cited_by_url`` and ``versions_url`` fields of collected records be
    pasted straight back into the CLI.

    :param value: a numeric id, or a Scholar URL containing one.
    :returns: the numeric id.
    :raises ValueError: when ``value`` holds no Scholar cluster id.
    """
    candidate = value.strip()
    if _DIGITS_RE.match(candidate):
        return candidate
    match = _ID_RE.search(candidate)
    if match is None:
        raise ValueError(f"no Scholar cites/cluster id found in {value!r}")
    return match.group(1)


def search_url(request: SearchRequest, start: int = 0, host: str = SCHOLAR_HOST) -> str:
    """Build the result-page URL for ``request`` at result offset ``start``.

    :param request: the listing and its filters.
    :param start: zero-based result offset; Google Scholar pages in steps of 10.
    :param host: Scholar host to hit, e.g. a regional mirror.
    :returns: absolute URL of the result page.
    """
    params: dict[str, str | int] = {"hl": request.language or "en"}
    if request.cites:
        params["cites"] = request.cites
    if request.cluster:
        params["cluster"] = request.cluster
    if request.query:
        params["q"] = request.query
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


def parse_user_id(value: str) -> str:
    """Accept a bare profile id or any Scholar URL carrying ``user=``.

    :param value: a 12-character profile id, or a profile URL containing one.
    :returns: the profile id.
    :raises ValueError: when ``value`` holds no Scholar profile id.
    """
    candidate = value.strip()
    if _USER_ID_RE.match(candidate):
        return candidate
    match = _USER_RE.search(candidate)
    if match is None:
        raise ValueError(f"no Scholar profile id (user=...) found in {value!r}")
    return match.group(1)


def author_url(
    request: AuthorRequest,
    cstart: int = 0,
    host: str = SCHOLAR_HOST,
    page_size: int = AUTHOR_PAGE_SIZE,
) -> str:
    """Build the profile-page URL for ``request`` at publication offset ``cstart``.

    :param request: the profile to read.
    :param cstart: zero-based publication offset.
    :param host: Scholar host to hit.
    :param page_size: publications requested per page, capped by Scholar at 100.
    :returns: absolute URL of the profile page.
    """
    params: dict[str, str | int] = {
        "user": request.user_id,
        "hl": request.language or "en",
        "cstart": cstart,
        "pagesize": page_size,
    }
    if request.sort_by_year:
        params["sortby"] = "pubdate"
    return f"{host}/citations?{urlencode(params)}"


def cite_url(cluster_id: str, host: str = SCHOLAR_HOST, language: str | None = None) -> str:
    """Build the "Cite" popup URL for one result cluster.

    The popup carries the formatted citation strings and the export links, including
    the signed ``scholar.bib`` link that cannot be constructed directly.

    :param cluster_id: Scholar cluster id of the record.
    :param host: Scholar host to hit.
    :param language: interface language (``hl``).
    :returns: absolute URL of the cite popup fragment.
    """
    params = {
        "q": f"info:{cluster_id}:scholar.google.com/",
        "output": "cite",
        "scirp": 0,
        "hl": language or "en",
    }
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


def redact_url(url: str) -> str:
    """Strip session material from a URL so it can be written to a log.

    A challenge URL carries the signed parameters that identify the session which triggered
    it, next to the query that was being requested. The query is the evidence worth keeping;
    everything else is replaced.

    :param url: the URL as the browser reported it.
    :returns: the URL with unrecognized parameter values replaced by ``REDACTED``.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url
    readable = {"hl"} if parts.path.startswith(CHALLENGE_PATH) else READABLE_PARAMS
    pairs = [
        (name, value if name in readable else "REDACTED")
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs), ""))
