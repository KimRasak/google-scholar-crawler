r"""Turning a dumped Scholar page into a committed regression fixture.

Hand-written fixtures prove the parser's logic but not that it still fits Scholar's real
markup. These fixtures are sanitized copies of pages the crawler actually loaded, so a
layout change breaks a test the next time they are refreshed — and until then the parser
keeps being checked against real structure rather than an idealisation of it.

Sanitizing removes everything a committed file should not carry (scripts, styles, image
sources, signed URL parameters) and trims the page to a few cards, while leaving the
element structure, class names and link shapes the parser depends on untouched.

Refresh a fixture with a dump from a real run::

    scholar-crawler -q "graph attention networks" -p 1 -n 2 --bibtex out/x.bib \\
        --dump-html out/dump -o out/d.jsonl
    python3 -m tests.sanitize out/dump/<file>.html tests/pages/results.html 6
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

DROP_TAGS = ("script", "style", "noscript", "link", "svg", "iframe")
"""Tags removed wholesale: none of them carry structure the parser reads."""

REDACTED_PARAMS = frozenset(
    {"scisig", "scisdr", "sig", "ved", "ei", "sxsrf", "uact", "usg", "gs_lcp", "ct", "cd", "authuser"}
)
"""Query parameters whose values are session-bound or signed, so they are replaced."""

CARD_SELECTORS = ("div.gs_r.gs_or.gs_scl", "tr.gsc_a_tr")
"""Repeated records — result cards and profile rows — trimmed down to a few examples."""

SENSITIVE_FIELDS = frozenset({"xsrf", "csrf", "token", "sig", "scisig"})
"""Form field names whose values authenticate the session that dumped the page."""

NESTED_TOKEN = re.compile(
    r"(scisig|xsrf|csrf|scisdr|usg|sxsrf)(=|%3D)([A-Za-z0-9_%.\-]{8,})",
    re.IGNORECASE,
)
"""Signed values surviving inside URL-encoded parameters such as ``continue=``."""


def _redact_url(url: str) -> str:
    """Replace signed or session-bound query values in one URL.

    :param url: an absolute or relative URL from the page.
    :returns: the URL with sensitive parameter values replaced by ``REDACTED``.
    """
    if not url or url.startswith(("#", "javascript:", "data:")):
        return url
    parts = urlsplit(url)
    if not parts.query:
        return url
    query = [
        (key, "REDACTED" if key in REDACTED_PARAMS else value) for key, value in parse_qsl(parts.query)
    ]
    return urlunsplit(parts._replace(query=urlencode(query, safe=":/,+")))


def sanitize(html: str, *, max_cards: int = 3) -> str:
    """Turn a dumped page into a committable fixture.

    :param html: the page HTML as dumped by ``--dump-html``.
    :param max_cards: how many repeated records to keep.
    :returns: the sanitized HTML.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for image in soup.find_all("img"):
        if isinstance(image, Tag):
            image["src"] = "about:blank"
    for element in soup.find_all(True):
        if not isinstance(element, Tag):
            continue
        for attribute in ("href", "action", "data-href", "data-clk"):
            value = element.get(attribute)
            if isinstance(value, str):
                element[attribute] = _redact_url(value)
        for attribute in ("onclick", "onload", "nonce", "jsaction", "jscontroller", "jsname"):
            if attribute in element.attrs:
                del element[attribute]
    for selector in CARD_SELECTORS:
        for extra in soup.select(selector)[max_cards:]:
            extra.decompose()
    for field in soup.find_all("input"):
        sensitive = isinstance(field, Tag) and str(field.get("name", "")).casefold() in SENSITIVE_FIELDS
        if sensitive and field.get("value"):
            field["value"] = "REDACTED"
    text = str(soup)
    text = NESTED_TOKEN.sub(lambda match: f"{match.group(1)}{match.group(2)}REDACTED", text)
    # Verified-email lines are the one piece of contact information Scholar renders.
    text = re.sub(r"Verified email at [\w.\-]+", "Verified email at example.edu", text)
    return text.strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Sanitize a dumped page into a fixture file.

    :param argv: ``[source, destination, max_cards?]``; defaults to ``sys.argv[1:]``.
    :returns: process exit code — 0 on success, 2 on a usage error.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (2, 3):
        print("usage: python3 -m tests.sanitize DUMPED.html FIXTURE.html [MAX_CARDS]", file=sys.stderr)
        return 2
    source, destination = Path(args[0]), Path(args[1])
    max_cards = int(args[2]) if len(args) == 3 else 3
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        sanitize(source.read_text(encoding="utf-8"), max_cards=max_cards), encoding="utf-8"
    )
    print(f"{source} -> {destination} ({destination.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
