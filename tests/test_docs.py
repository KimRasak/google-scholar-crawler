"""The two READMEs: their navigation must point at sections that exist.

The Chinese README is the primary document and the English one mirrors it, so both are
checked, and the mirror is required to carry the same sections.
"""

from __future__ import annotations

import re
import shlex
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import build_parser as crawler_parser  # noqa: E402
from scholar_crawler.digest import build_parser as digest_parser  # noqa: E402
from scholar_crawler.models import ScholarResult  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
READMES = (ROOT / "README.md", ROOT / "README.en.md")
AGENTS = ROOT / "AGENTS.md"
LINK = re.compile(r"\]\(#([^)]+)\)")
SHELL = re.compile(r"```sh\n(.*?)```", re.DOTALL)


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
    # One row per situation, and each row must send the reader somewhere. The separator row is
    # dropped by shape, not by looking for "---", which also appears inside anchors.
    for text, heading in ((chinese, "## 从哪读起"), (english, "## Where to start")):
        section = text.split(heading, 1)[1].split("\n## ", 1)[0]
        rows = [
            line
            for line in section.splitlines()
            if line.startswith("| ") and set(line) - set("| -")
        ]
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


def _commands(text: str) -> list[str]:
    """Collect the shell commands a document tells a reader to run.

    :param text: the document.
    :returns: one command per line, comments and continuations stripped.
    """
    commands = []
    for block in SHELL.findall(text):
        for line in block.splitlines():
            command = line.split("#", 1)[0].strip()
            if command.startswith(("scholar-crawler", "scholar-digest")):
                commands.append(command)
    return commands


def test_the_agent_guide_stays_short_enough_to_be_read_in_full() -> None:
    # It exists because README.md is 700 lines; a guide that grows into a second README
    # defeats its own purpose.
    lines = AGENTS.read_text(encoding="utf-8").splitlines()
    assert len(lines) < 150, f"AGENTS.md is {len(lines)} lines; keep it an interface, not a manual"


def test_every_command_in_the_agent_guide_parses() -> None:
    for command in _commands(AGENTS.read_text(encoding="utf-8")):
        argv = shlex.split(command)
        parser = crawler_parser() if argv[0] == "scholar-crawler" else digest_parser()
        assert parser.parse_args(argv[1:]) is not None, command


def test_both_readmes_point_at_the_agent_guide() -> None:
    for path in READMES:
        assert "AGENTS.md" in path.read_text(encoding="utf-8"), path.name

def test_the_agent_guide_lists_exactly_the_keys_a_record_carries() -> None:
    # An agent builds its parsing against this list, so a field added to the record without a
    # line in AGENTS.md is a silent interface change.
    text = AGENTS.read_text(encoding="utf-8")
    listed = text.split("One record carries exactly these keys:", 1)[1].split(". Absent", 1)[0]
    documented = set(re.findall(r"`([a-z_]+)`", listed))
    carried = set(
        ScholarResult(
            cluster_id="1",
            position=1,
            title="t",
            link=None,
            resource_link=None,
            resource_type=None,
            byline="",
            authors=None,
            venue=None,
            year=None,
            snippet="",
            cited_by_count=None,
            cited_by_url=None,
            versions_count=None,
            versions_url=None,
            related_url=None,
            citation_only=False,
        ).to_dict()
    )
    assert documented == carried, f"AGENTS.md is out of step: {documented ^ carried}"
