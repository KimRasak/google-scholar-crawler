"""What a Scholar venue string contains.

A result card and a profile row put several facts on one grey line, and the venue part arrives
with volume, issue, pages and sometimes the year appended to the name: ``nature 521 (7553),
436-444, 2015``. Grouping needs the name alone, a bibliography needs each part in its own field,
and both read them here rather than each carrying its own regular expression.

Scholar also elides long venue names, at either end. Whether the name was cut is part of what
the string says, so it is reported rather than quietly dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text import TRUNCATION

TAIL = re.compile(
    r"\s+(?P<volume>\d+)"
    r"(?:\s*\((?P<number>[^)]*)\))?"
    r"(?:,\s*(?P<pages>\d+\s*[-–]\s*\d+|\d+))?"
    r"(?:,\s*(?P<year>(?:19|20)\d{2}))?"
    r"\s*$"
)
"""Volume, issue, pages and year as Scholar appends them to a venue name.

The volume must follow whitespace, so a name that is itself a number (a bare year on a card
with no venue) stays the name instead of becoming a volume with nothing in front of it.
"""


@dataclass(slots=True, frozen=True)
class Venue:
    """One venue string, read apart.

    :param name: the journal, conference or publisher name, without the numeric tail.
    :param volume: volume number, when the string carried one.
    :param number: issue, when the string carried one in parentheses.
    :param pages: page range, when the string carried one.
    :param cut_head: True when Scholar elided the beginning of the name.
    :param cut_tail: True when Scholar elided the end of the name.
    """

    name: str
    volume: str | None
    number: str | None
    pages: str | None
    cut_head: bool
    cut_tail: bool

    @property
    def cut(self) -> bool:
        """Report whether Scholar elided the name at all.

        :returns: True when either end was cut.
        """
        return self.cut_head or self.cut_tail


def split_venue(venue: str) -> Venue:
    """Read a venue string apart into its name and its numeric tail.

    :param venue: the venue as parsed from a card or a profile row.
    :returns: the parts it carries.
    """
    stripped = venue.strip()
    cut_head = stripped.startswith(TRUNCATION)
    cut_tail = stripped.endswith(TRUNCATION)
    for mark in TRUNCATION:
        stripped = stripped.removeprefix(mark).removesuffix(mark)
    cleaned = stripped.strip(" ,.")
    match = TAIL.search(cleaned)
    if match is None:
        return Venue(
            name=cleaned, volume=None, number=None, pages=None, cut_head=cut_head, cut_tail=cut_tail
        )
    name = cleaned[: match.start()].strip(" ,.")
    return Venue(
        name=name or cleaned,
        volume=match.group("volume"),
        number=(match.group("number") or "").strip() or None,
        pages=(match.group("pages") or "").replace(" ", "") or None,
        cut_head=cut_head,
        cut_tail=cut_tail,
    )
