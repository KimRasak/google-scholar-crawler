"""The two commands as one interface: no flag means two things, no name means two flags.

Forty crawler flags and twenty digest flags accumulated one feature at a time, and the way
that goes wrong is silent: one option quietly governing four unrelated outputs, or the same
idea spelled differently in each command. These tests read the parsers themselves, so a new
flag that breaks the pattern fails here rather than confusing someone later.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import build_parser as crawler_parser  # noqa: E402
from scholar_crawler.digest import build_parser as digest_parser  # noqa: E402


# argparse exposes no public view of its options; _actions is the only way to read them back.
def _options(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    """Map every long option of a parser to its action.

    :param parser: the parser to read.
    :returns: actions keyed by long option string, help excluded.
    """
    found = {}
    for action in parser._actions:
        for option in action.option_strings:
            if option.startswith("--") and option != "--help":
                found[option] = action
    return found


CRAWLER = _options(crawler_parser())
DIGEST = _options(digest_parser())
SHARED = sorted(set(CRAWLER) & set(DIGEST))


def test_the_two_commands_do_share_flags_so_the_comparison_is_not_empty() -> None:
    assert "--bibtex" in SHARED
    assert "--year-from" in SHARED
    assert "--out" in SHARED


@pytest.mark.parametrize("option", SHARED)
def test_a_shared_flag_takes_the_same_kind_of_value_in_both_commands(option: str) -> None:
    crawler, digest = CRAWLER[option], DIGEST[option]
    assert crawler.type == digest.type, f"{option} parses differently in the two commands"
    assert crawler.nargs == digest.nargs, f"{option} takes a different number of values"
    # Defaults are allowed to differ: a crawl must write its records somewhere, while every
    # digest output is opt-in.


@pytest.mark.parametrize("option", SHARED)
def test_a_shared_flag_is_explained_in_both_commands(option: str) -> None:
    for name, action in (("crawler", CRAWLER[option]), ("digest", DIGEST[option])):
        assert action.help and action.help != argparse.SUPPRESS, f"{option} is unexplained in {name}"


@pytest.mark.parametrize("parser", [crawler_parser(), digest_parser()], ids=["crawler", "digest"])
def test_every_flag_is_explained(parser: argparse.ArgumentParser) -> None:
    # argparse.SUPPRESS is a truthy string, so "has help" is not enough: a suppressed flag
    # exists, accepts a value, and appears nowhere in --help.
    rendered = parser.format_help()
    for option, action in _options(parser).items():
        assert action.help, f"{option} has no help text"
        assert action.help != argparse.SUPPRESS, f"{option} hides itself from --help"
        assert option in rendered, f"{option} never appears in --help"


@pytest.mark.parametrize("parser", [crawler_parser(), digest_parser()], ids=["crawler", "digest"])
def test_every_flag_sits_in_a_named_group_with_a_description(
    parser: argparse.ArgumentParser
) -> None:
    # A flat list of forty options tells nobody where to start.
    grouped = set()
    for group in parser._action_groups:
        if group.title in ("options", "positional arguments"):
            continue
        assert group.description, f"group {group.title!r} does not say when to use it"
        grouped.update(
            option
            for action in group._group_actions
            for option in action.option_strings
            if option.startswith("--")
        )
    ungrouped = set(_options(parser)) - grouped
    assert not ungrouped, f"these flags belong in a group: {sorted(ungrouped)}"


@pytest.mark.parametrize("parser", [crawler_parser(), digest_parser()], ids=["crawler", "digest"])
def test_a_flag_with_a_default_states_it(parser: argparse.ArgumentParser) -> None:
    interesting = (int, float, str)
    for option, action in _options(parser).items():
        if action.default in (None, False, 0, [], "") or not isinstance(action.default, interesting):
            continue
        assert "default" in (action.help or ""), f"{option} defaults to {action.default!r} silently"


def test_the_counting_flags_of_the_digest_each_govern_one_output() -> None:
    # --top used to size the overview, the report, the stale list and the network list at once,
    # so asking for a shorter terminal list silently shortened the written report.
    assert "every printed list" in (DIGEST["--top"].help or "")
    assert "--report" in (DIGEST["--report-top"].help or "")
    assert "--refresh-list" in (DIGEST["--refresh-limit"].help or "")
    assert DIGEST["--top"].default != DIGEST["--report-top"].default


def test_a_shorter_terminal_list_leaves_the_written_report_alone(tmp_path: Path) -> None:
    args = digest_parser().parse_args(["x.jsonl", "--top", "2"])
    assert args.top == 2
    assert args.report_top == 15


def test_size_thresholds_and_counts_are_named_apart() -> None:
    assert "--min-citations" in DIGEST, "a threshold reads as a minimum, not as a count"
    assert "cited fewer times" in (DIGEST["--min-citations"].help or "")
    assert "groups to list" in (DIGEST["--groups"].help or "")


@pytest.mark.parametrize(
    ("parser", "options"), [(crawler_parser(), CRAWLER), (digest_parser(), DIGEST)]
)
def test_the_usage_line_stays_short_and_names_real_flags(
    parser: argparse.ArgumentParser, options: dict[str, argparse.Action]
) -> None:
    # Usage is written by hand because argparse's generated one fills the screen; the price of
    # writing it is that a renamed flag could leave it lying, so the test reads it back.
    usage = parser.format_usage().removeprefix("usage: ").splitlines()
    assert len(usage) <= 3, "usage is the shapes a run takes, not the flag list"
    for line in usage:
        assert line.strip().startswith(parser.prog)
    named = re.findall(r"--[a-z][a-z-]+", parser.format_usage())
    assert named, "a usage line that names no flag says nothing"
    for option in named:
        assert option in options, f"usage names {option}, which the parser does not define"
