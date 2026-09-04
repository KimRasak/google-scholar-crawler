"""Fitting text into the aligned columns this tool prints.

Titles are longer than any column, so several reports cut them. Cutting without saying so
produces lines like ``... from the perspective of representa``, which reads as damaged data
rather than as a shortened line; every cut here is marked instead.
"""

from __future__ import annotations

ELLIPSIS = "…"
"""Appended to text that did not fit, so a cut is never mistaken for the value."""

TRUNCATION = (ELLIPSIS, "...")
"""How Scholar marks its own elisions: an author list or venue arrives already cut."""


def clip(text: str, width: int) -> str:
    """Shorten text to a column width, marking it when it was cut.

    :param text: the text to fit.
    :param width: the column width, counting the ellipsis.
    :returns: the text unchanged, or its beginning followed by :data:`ELLIPSIS`.
    :raises ValueError: when the width leaves no room for any text.
    """
    if width < 2:
        raise ValueError(f"width {width} leaves no room for text and an ellipsis")
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + ELLIPSIS


def counted(count: int, noun: str) -> str:
    """Write a count with its noun in the right number.

    :param count: how many.
    :param noun: the singular noun, pluralized by adding ``s``.
    :returns: the phrase, with thousands separated.
    """
    return f"{count:,} {noun}" if count == 1 else f"{count:,} {noun}s"
