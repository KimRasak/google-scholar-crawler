"""Building BibTeX entries from records already collected.

Exporting BibTeX during a crawl costs two extra page loads per record, which is the
expensive part of a run. Everything Scholar shows on a result card — title, authors, venue,
year, link — is already stored, so a usable entry can be assembled offline afterwards.

These entries are reconstructions, not Scholar's own export: author lists that Scholar
truncated stay truncated (marked with ``and others``), and the venue is whatever the result
card showed. When a record was exported during the crawl its key is reused, so a synthesized
file and a crawled one refer to the same works by the same names.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Record = dict[str, Any]

TRANSLITERATIONS = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ß": "ss", "æ": "ae", "œ": "oe", "ð": "d"})
"""Letters that carry no Unicode decomposition, so stripping accents alone would drop them."""

TITLE_STOPWORDS = frozenset(
    {"a", "an", "the", "on", "of", "in", "for", "and", "to", "with", "from", "by", "at", "is", "are"}
)
"""Words skipped when a key needs the first meaningful word of a title."""

PROCEEDINGS = re.compile(r"\b(proceedings|conference|workshop|symposium|congress|meeting)\b", re.I)
"""Venue words that make a record a conference paper rather than a journal article."""

ESCAPES = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_"}
"""LaTeX special characters escaped inside field values."""

DANGLING_ARXIV = re.compile(r"\s+arxiv$", re.IGNORECASE)
"""What is left of ``arXiv preprint arXiv:1234.5678`` once Scholar truncates the identifier."""


def venue_field(record: Record) -> str:
    """Read the venue as it should appear in a bibliography.

    Volume, issue and page numbers are kept — they are real bibliographic data — while
    Scholar's truncation marker and the identifier stub it leaves behind are dropped.

    :param record: a stored record.
    :returns: the venue, or an empty string when the record carries none.
    """
    cleaned = (record.get("venue") or "").strip().strip("…").strip(" ,.")
    return DANGLING_ARXIV.sub("", cleaned).strip()


def ascii_slug(text: str) -> str:
    """Reduce text to lowercase ASCII letters and digits.

    :param text: any text, possibly accented.
    :returns: the transliterated slug, empty when nothing survives.
    """
    folded = unicodedata.normalize("NFKD", text.casefold().translate(TRANSLITERATIONS))
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]", "", stripped)


def surname(byline: str) -> str:
    """Read the first author's surname from a Scholar byline.

    Scholar abbreviates given names, so the surname is the last word of the first name it
    lists — ``P Veličković, G Cucurull`` gives ``Veličković``.

    :param byline: the record's author list, as Scholar renders it.
    :returns: the surname, or an empty string when the byline carries none.
    """
    first = byline.split(" - ")[0].split(",")[0].strip().strip("…").strip()
    words = [word for word in first.split() if word]
    return words[-1] if words else ""


def authors_field(record: Record) -> tuple[str, bool]:
    """Format the author list for BibTeX.

    :param record: a stored record.
    :returns: the ``author`` value, and whether Scholar had truncated the list.
    """
    raw = (record.get("authors") or (record.get("byline") or "").split(" - ")[0] or "").strip()
    truncated = raw.endswith("…") or raw.endswith("...")
    names = [name.strip().strip("…").strip() for name in raw.split(",")]
    names = [name for name in names if name]
    if truncated:
        names.append("others")
    return " and ".join(names), truncated


def entry_type(record: Record) -> str:
    """Choose the BibTeX entry type for a record.

    :param record: a stored record.
    :returns: ``article``, ``inproceedings`` or ``misc``.
    """
    venue = venue_field(record)
    if not venue:
        return "misc"
    return "inproceedings" if PROCEEDINGS.search(venue) else "article"


def make_key(record: Record, used: set[str]) -> str:
    """Choose a citation key for a record, reusing the crawled one when there is one.

    :param record: a stored record.
    :param used: keys already taken; the chosen key is added to it.
    :returns: a key unique within ``used``.
    """
    stored = (record.get("extra") or {}).get("bibtex_key")
    base = str(stored) if stored else ""
    if not base:
        name = ascii_slug(surname(record.get("authors") or record.get("byline") or "")) or "anon"
        year = str(record.get("year") or "")
        words = [word for word in re.findall(r"[\w']+", record.get("title") or "") if word]
        meaningful = next((word for word in words if ascii_slug(word) not in TITLE_STOPWORDS), "")
        base = f"{name}{year}{ascii_slug(meaningful)}" or "record"
    key = base
    suffix = ord("a")
    while key in used:
        key = f"{base}{chr(suffix)}"
        suffix += 1
    used.add(key)
    return key


def _escape(value: str) -> str:
    """Make a value safe to place inside a braced BibTeX field.

    :param value: the raw field value.
    :returns: the value with braces flattened and LaTeX specials escaped.
    """
    flattened = value.replace("{", "(").replace("}", ")").replace("\\", "/")
    return "".join(ESCAPES.get(char, char) for char in flattened)


def synthesize_entry(record: Record, key: str) -> str:
    """Build one BibTeX entry from a stored record.

    :param record: a stored record; a record without a title cannot be described.
    :param key: the citation key to use.
    :returns: the entry text, ending with a newline.
    """
    kind = entry_type(record)
    # Braced twice so a style that lowercases titles cannot touch Scholar's capitalization.
    fields: list[tuple[str, str]] = [("title", f"{{{_escape(record.get('title') or '')}}}")]
    authors, _truncated = authors_field(record)
    if authors:
        fields.append(("author", _escape(authors)))
    venue = venue_field(record)
    if venue:
        fields.append(("booktitle" if kind == "inproceedings" else "journal", _escape(venue)))
    if record.get("year"):
        fields.append(("year", str(record["year"])))
    if record.get("link"):
        fields.append(("url", _escape(str(record["link"]))))
    if record.get("cited_by_count") is not None:
        fields.append(("note", f"cited by {record['cited_by_count']} on Google Scholar"))
    body = "".join(f"  {name} = {{{value}}},\n" for name, value in fields)
    return f"@{kind}{{{key},\n{body}}}\n"


@dataclass(slots=True)
class BibtexReport:
    """Outcome of writing a synthesized bibliography.

    :param written: entries written to the file.
    :param reused_keys: entries whose key came from a crawl-time export.
    :param truncated_authors: entries whose author list Scholar had cut short.
    :param skipped: records that carried no title, so nothing could be written.
    """

    written: int
    reused_keys: int
    truncated_authors: int
    skipped: int

    def describe(self) -> str:
        """Summarize the export in one phrase.

        :returns: the counts worth printing next to the file name.
        """
        parts = [f"{self.reused_keys} keys from the crawl", f"{self.written - self.reused_keys} generated"]
        if self.truncated_authors:
            parts.append(f"{self.truncated_authors} truncated author lists")
        if self.skipped:
            parts.append(f"{self.skipped} skipped without a title")
        return ", ".join(parts)


def write_bibtex(records: list[Record], path: Path) -> BibtexReport:
    """Write a bibliography for ``records``, replacing the file if it exists.

    :param records: records to describe, in the order they should appear.
    :param path: destination ``.bib`` file.
    :returns: what was written.
    """
    used: set[str] = set()
    entries: list[str] = []
    report = BibtexReport(written=0, reused_keys=0, truncated_authors=0, skipped=0)
    for record in records:
        if not (record.get("title") or "").strip():
            report.skipped += 1
            continue
        stored_key = bool((record.get("extra") or {}).get("bibtex_key"))
        key = make_key(record, used)
        entries.append(synthesize_entry(record, key))
        report.written += 1
        report.reused_keys += 1 if stored_key else 0
        report.truncated_authors += 1 if authors_field(record)[1] else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(entries), encoding="utf-8")
    return report
