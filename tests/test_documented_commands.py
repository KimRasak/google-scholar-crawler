"""Running the commands the documentation prints.

A documented command that no longer works is worse than no documentation, because it is
followed literally — by a person copying the quickstart, and by an agent reading AGENTS.md.

Every ``scholar-crawler``/``scholar-digest`` command in either README or in AGENTS.md is either
executed here in a scratch workspace seeded with whatever files it reads, or accounted for by a
named reason why it cannot run offline. A new command escapes neither branch.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import main as crawler_main  # noqa: E402
from scholar_crawler.digest import main as digest_main  # noqa: E402
from scholar_crawler.parser import parse_result_page  # noqa: E402
from tests.fixtures import RESULT_PAGE_HTML  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "README.md", ROOT / "README.en.md", ROOT / "AGENTS.md")
SHELL = re.compile(r"```sh\n(.*?)```", re.DOTALL)

OFFLINE_MODES = ("--recipes", "--doctor", "--dry-run", "--show-state", "--forget")
"""Crawler modes that collect nothing, so a test can run them for real."""

NEEDS_NETWORK = {
    "--install-browser": "downloads Chromium from Playwright's CDN",
    "--self-check": "sends one request to Scholar",
    "--rehearse-handoff": "opens a browser window and waits for a person",
}
"""Crawler flags that reach past this process, with the reason each one does."""

A_CRAWL = "sends requests to Scholar"
"""Reason for a documented command that is simply a crawl."""

WRITTEN_BY = (
    "-o",
    "--out",
    "--csv",
    "--bibtex",
    "--report",
    "--refresh-list",
    "--dump-html",
    "--state",
    "--challenge-log",
)
"""Flags whose value is a file the command writes, so it must not be seeded."""


def _documented() -> dict[str, list[str]]:
    """Collect every documented command and the output printed under it.

    :returns: command mapped to the lines the READMEs show it printing.
    """
    found: dict[str, list[str]] = {}
    for path in DOCS:
        for block in SHELL.findall(path.read_text(encoding="utf-8")):
            current = ""
            for raw in block.replace("\\\n", " ").splitlines():
                line = raw.strip()
                stripped = line[2:].strip() if line.startswith("$ ") else line
                command = stripped.split("#", 1)[0].strip()
                if command.startswith(("scholar-crawler", "scholar-digest")):
                    current = command
                    found.setdefault(command, [])
                elif current and line:
                    found[current].append(line)
    return found


def _reason(command: str) -> str:
    """Say why a command cannot run in this test, or return an empty string.

    :param command: the documented command.
    :returns: the reason it reaches the network, or "" when it runs offline.
    """
    argv = shlex.split(command)
    if argv[0] == "scholar-digest":
        return ""
    for flag, reason in NEEDS_NETWORK.items():
        if flag in argv:
            return reason
    return "" if any(mode in argv for mode in OFFLINE_MODES) else A_CRAWL


def _records() -> list[dict[str, object]]:
    """Build the records a seeded input file holds.

    :returns: the fixture page's records as dictionaries.
    """
    page = parse_result_page(RESULT_PAGE_HTML, query="graph attention networks")
    return [result.to_dict() for result in page.results]


def _write(target: Path) -> None:
    """Create one input file the documentation expects to already exist.

    :param target: the path to create; a ``.txt`` file holds cluster ids, a ``.jsonl`` records.
    """
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix == ".txt":
        target.write_text("AAA111\n", encoding="utf-8")
    else:
        target.write_text(
            "".join(json.dumps(record) + "\n" for record in _records()), encoding="utf-8"
        )


def _seed(workspace: Path, argv: list[str]) -> list[str]:
    """Create every file the command reads and expand its globs.

    :param workspace: directory the command runs in.
    :param argv: the command's arguments, without the program name.
    :returns: the arguments with globs replaced by the paths they match.
    """
    for index, token in enumerate(argv):
        previous = argv[index - 1] if index else ""
        if previous in WRITTEN_BY or "*" in token or not token.endswith((".jsonl", ".txt")):
            continue
        target = workspace / token
        if target.exists():
            continue
        _write(target)
    if "--collection" in argv:
        _write(workspace / argv[argv.index("--collection") + 1] / "seeded.jsonl")
    expanded = []
    for token in argv:
        if "*" not in token:
            expanded.append(token)
            continue
        _write(workspace / Path(token).parent / "seeded.jsonl")
        expanded.extend(sorted(str(path.relative_to(workspace)) for path in workspace.glob(token)))
    return expanded


OFFLINE = sorted(command for command in _documented() if not _reason(command))
NETWORKED = sorted(command for command in _documented() if _reason(command))


@pytest.mark.parametrize("command", OFFLINE)
def test_every_offline_command_in_the_documentation_runs(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    argv = _seed(tmp_path, shlex.split(command)[1:])
    run = crawler_main if command.startswith("scholar-crawler") else digest_main

    code = run(argv)
    printed = capsys.readouterr().out
    if "--doctor" in argv:
        # --doctor reports the machine it runs on, so its exit code is that machine's verdict:
        # a CI runner without Chrome installed is a correct 1.
        assert code in (0, 1), f"{command}\n{printed}"
        assert "[doctor]" in printed
    else:
        assert code == 0, f"{command}\n{printed}"
    assert printed.strip(), f"{command} printed nothing"

    # A stale URL in the docs gets copied into a browser and quietly searches for something else.
    for line in _documented()[command]:
        if " -> https://" in line:
            assert line in printed, f"{command} no longer prints: {line}"


def test_every_documented_command_is_either_run_here_or_explained() -> None:
    assert len(OFFLINE) >= 10, "the offline half of the documentation should be exercised"
    for command in NETWORKED:
        assert _reason(command) in {A_CRAWL, *NEEDS_NETWORK.values()}, command
    assert any(_reason(command) == A_CRAWL for command in NETWORKED)
    for flag in NEEDS_NETWORK:
        assert any(flag in command for command in NETWORKED), f"{flag} left the documentation"
