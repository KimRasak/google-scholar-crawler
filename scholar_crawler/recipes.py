"""Ready-to-run commands for the tasks this tool exists for.

The crawler has more than thirty flags, most of which exist to be left alone. What a new
user needs is not the flag list but a handful of complete commands that work as written, so
``--recipes`` prints these and a run started with no arguments points at them.

This module also spells the flags for a target the tool already knows about, so ``--show-state``
can hand back the command that continues it instead of a name to translate by hand.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from .models import AuthorRequest, SearchRequest
from .storage import DEFAULT_STATE_PATH

DEFAULT_LANGUAGE = "en"
"""Interface language a command need not spell out, matching ``--lang``'s own default."""


@dataclass(slots=True, frozen=True)
class Recipe:
    """One complete command, with the reason to run it.

    :param purpose: what the command is for, in one line.
    :param command: the command as it should be typed.
    :param note: what to expect, or the one thing worth knowing before running it.
    """

    purpose: str
    command: str
    note: str


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        purpose="Collect one topic — start here",
        command='scholar-crawler -q "graph attention networks" -p 3 -o out/gat.jsonl',
        note="3 pages, 10 records each, about a minute; clear any challenge in the window it opens",
    ),
    Recipe(
        purpose="Check that this machine can run a crawl at all",
        command="scholar-crawler --doctor",
        note="no requests; reports Python, the libraries, the browser and the directories",
    ),
    Recipe(
        purpose="Read a command back and cost it before starting",
        command='scholar-crawler -q "diffusion models" -p 5 --bibtex out/refs.bib --dry-run',
        note="no requests; names the files it would touch, the flags that cancel each other, "
        "the page loads and the duration",
    ),
    Recipe(
        purpose="Check the parser against Scholar before trusting a long run",
        command="scholar-crawler --self-check",
        note="one request; reports per field whether Scholar's layout still parses",
    ),
    Recipe(
        purpose="Rehearse the human takeover without touching Google",
        command="scholar-crawler --rehearse-handoff",
        note="a local challenge page; press the button to prove the resume path works",
    ),
    Recipe(
        purpose="Keep the flags you always pass in a file",
        command="scholar-crawler --config scholar.toml --dry-run",
        note="cp scholar.toml.example scholar.toml first; --dry-run names where every value came from",
    ),
    Recipe(
        purpose="Collect from a script or an agent, and parse the result",
        command='scholar-crawler -q "graph attention networks" -p 2 --json',
        note="one JSON object on stdout, every progress line on stderr; see AGENTS.md",
    ),
    Recipe(
        purpose="Search several topics into one file",
        command="scholar-crawler --queries-file queries.txt -p 2 -o out/all.jsonl",
        note="one query per line; # comments are ignored",
    ),
    Recipe(
        purpose="Collect an author's publications and profile stats",
        command="scholar-crawler --author kukA0LcAAAAJ -n 200 -o out/author.jsonl",
        note="accepts a profile URL too; 100 publications per page load",
    ),
    Recipe(
        purpose="Follow the citation graph outward from a paper",
        command="scholar-crawler --cites 2960712678066186980 --follow-cites 1 --follow-breadth 5 -p 2",
        note="expansion multiplies requests; run --dry-run first",
    ),
    Recipe(
        purpose="Continue an interrupted collection",
        command="scholar-crawler --queries-file queries.txt -p 5 --resume",
        note="each target continues from the offset in the state file; --show-state first",
    ),
    Recipe(
        purpose="Check how much of what you collected can be trusted",
        command="scholar-digest out/*.jsonl --audit",
        note="no requests; flags implausible years, page-range venues and missing fields",
    ),
    Recipe(
        purpose="See who cites whom inside what you already collected",
        command="scholar-digest out/*.jsonl --network",
        note="no requests; edges come from --cites listings already on disk",
    ),
    Recipe(
        purpose="Find out how stale a collection is, and what to collect again",
        command="scholar-digest out/*.jsonl --stale 60 --refresh-list out/refresh.txt",
        note="no requests; feed the file back with scholar-crawler --clusters-file, one load per id",
    ),
    Recipe(
        purpose="Keep a collection current and see what changed since last time",
        command="scholar-digest --collection out --since out/merged.jsonl -o out/merged.jsonl",
        note="no requests; the folder is the unit, and the merge it writes is not one of its inputs",
    ),
    Recipe(
        purpose="Turn what you collected into a readable overview and a bibliography, offline",
        command="scholar-digest out/*.jsonl --report out/report.md --bibtex out/refs.bib",
        note="no requests at all; merges duplicates across files first",
    ),
)
"""The commands worth knowing.

The first one collects papers, because that is what a reader came for; a list that opened with
three diagnostics would answer a question nobody asked. After it come the checks, then roughly
safest to most expensive.
"""


def render(recipes: tuple[Recipe, ...] = RECIPES) -> list[str]:
    """Format recipes for the terminal.

    :param recipes: the recipes to print.
    :returns: printable lines, one recipe per three lines.
    """
    lines: list[str] = []
    for index, recipe in enumerate(recipes, start=1):
        lines.append(f"{index}. {recipe.purpose}")
        lines.append(f"   $ {recipe.command}")
        lines.append(f"     {recipe.note}")
    return lines


def getting_started(count: int = 3) -> list[str]:
    """Format the first few recipes, for a run that was given nothing to do.

    The first one collects, so the shortest path out of an error message is a working crawl.

    :param count: how many recipes to show.
    :returns: printable lines.
    """
    return render(RECIPES[:count])


def resume_command(
    request: SearchRequest | AuthorRequest, state: Path = DEFAULT_STATE_PATH
) -> str:
    """Spell the command that continues a target the state file already holds.

    A cursor is only useful to someone who can reproduce the target it belongs to. Days later
    that means retyping the filters from a name like ``x [en, 2020-, reviews only]``, which is
    the translation this does instead.

    :param request: the target, as :func:`~scholar_crawler.models.parse_signature` rebuilt it.
    :param state: the state file holding its cursor, named when it is not the default.
    :returns: a complete command line, ready to paste.
    """
    parts = ["scholar-crawler"]
    if isinstance(request, AuthorRequest):
        parts += ["--author", request.user_id]
        # One flag covers both: a profile sorted by year is what --sort-by-date asks for there.
        if request.sort_by_year:
            parts.append("--sort-by-date")
    else:
        if request.query:
            parts += ["-q", request.query]
        if request.cites:
            parts += ["--cites", request.cites]
        if request.cluster:
            parts += ["--cluster", request.cluster]
        if request.year_low is not None:
            parts += ["--year-from", str(request.year_low)]
        if request.year_high is not None:
            parts += ["--year-to", str(request.year_high)]
        if request.sort_by_date:
            parts.append("--sort-by-date")
        if not request.include_citations:
            parts.append("--no-citations")
        if not request.include_patents:
            parts.append("--no-patents")
        if request.review_only:
            parts.append("--review-only")
    if request.language and request.language != DEFAULT_LANGUAGE:
        parts += ["--lang", request.language]
    parts.append("--resume")
    if state != DEFAULT_STATE_PATH:
        parts += ["--state", str(state)]
    return shlex.join(parts)
