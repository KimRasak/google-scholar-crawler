"""The two READMEs: their navigation must point at sections that exist.

The Chinese README is the primary document and the English one mirrors it, so both are
checked, and the mirror is required to carry the same sections.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
READMES = (ROOT / "README.md", ROOT / "README.en.md")
LINK = re.compile(r"\]\(#([^)]+)\)")


def _anchor(title: str) -> str:
    """Approximate GitHub's heading anchor for ``title``.

    :param title: heading text without its leading hashes.
    :returns: the anchor GitHub would generate.
    """
    kept = [
        char
        for char in title.strip().lower().replace("`", "")
        if char.isalnum() or char in "-_ " or unicodedata.category(char).startswith("L")
    ]
    return "".join(kept).replace(" ", "-")


def _headings(text: str) -> dict[str, str]:
    """Collect anchors for every heading in a document.

    :param text: the document.
    :returns: anchor mapped to heading text.
    """
    return {
        _anchor(line.lstrip("#").strip()): line.lstrip("#").strip()
        for line in text.splitlines()
        if line.startswith("#")
    }


def test_every_in_page_link_points_at_a_real_section() -> None:
    for path in READMES:
        text = path.read_text(encoding="utf-8")
        headings = _headings(text)
        links = set(LINK.findall(text))
        assert links, f"{path.name} lost its navigation"
        missing = sorted(link for link in links if link not in headings)
        assert not missing, f"{path.name} links to missing sections: {missing}"


def test_the_navigation_table_covers_the_situations_a_user_arrives_with() -> None:
    chinese = READMES[0].read_text(encoding="utf-8")
    english = READMES[1].read_text(encoding="utf-8")
    assert "## 从哪读起" in chinese
    assert "## Where to start" in english
    # One row per situation, and each row must send the reader somewhere.
    for text, heading in ((chinese, "## 从哪读起"), (english, "## Where to start")):
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("| ") and "---" not in line]
        assert len(rows) >= 7, heading
        assert all(LINK.search(row) for row in rows[1:]), heading


def test_both_readmes_document_the_same_module_layout() -> None:
    modules = {
        path: {
            line.split()[0]
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("  ") and line.strip().split()[0].endswith(".py")
        }
        for path in READMES
    }
    chinese, english = (modules[path] for path in READMES)
    assert chinese == english, f"layout differs: {chinese ^ english}"
    package = {path.name for path in (ROOT / "scholar_crawler").glob("*.py")} - {"__init__.py"}
    assert chinese == package, f"undocumented or stale modules: {chinese ^ package}"
