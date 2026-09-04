"""Reading a command back: what --dry-run states, and which combinations it catches."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import build_parser, build_targets, main  # noqa: E402
from scholar_crawler.crawler import DEFAULT_MAX_DELAY, DEFAULT_MIN_DELAY  # noqa: E402
from scholar_crawler.expand import FollowPolicy  # noqa: E402
from scholar_crawler.explain import Level, concerns_of, explain  # noqa: E402
from scholar_crawler.storage import StateStore, written_paths  # noqa: E402


def _explained(argv: list[str]) -> list[str]:
    """Explain a command line as the CLI would.

    :param argv: arguments after the program name.
    :returns: the printable lines.
    """
    args = build_parser().parse_args(argv)
    listings, authors = build_targets(args)
    follow = FollowPolicy(
        depth=args.follow_cites, breadth=args.follow_breadth, min_citations=args.follow_min_citations
    )
    from scholar_crawler.cli import _browser_options, _resolve_pacing

    return explain(args, listings, authors, follow, _resolve_pacing(args), _browser_options(args))


def _concerns(argv: list[str]) -> list[str]:
    """Collect the concern messages for a command line.

    :param argv: arguments after the program name.
    :returns: the rendered concerns.
    """
    return [line for line in _explained(argv) if line.startswith(("warn:", "note:"))]


@pytest.fixture(autouse=True)
def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every explanation pointed at a throwaway directory."""
    monkeypatch.chdir(tmp_path)


def test_the_explanation_reads_back_what_the_window_will_claim_to_be() -> None:
    # The line is the launch settings themselves, so it cannot drift from what gets launched.
    assert any(
        "Accept-Language de and reports its clock in Europe/Berlin (matching the language)" in line
        for line in _explained(["-q", "x", "--lang", "de"])
    )
    assert any(
        "Accept-Language en-US and reports its clock in Asia/Tokyo (yours)" in line
        for line in _explained(["-q", "x", "--timezone", "Asia/Tokyo"])
    )


def test_a_plain_run_is_described_without_any_concern() -> None:
    lines = _explained(["-q", "graph attention networks", "-p", "3"])
    assert "crawling 1 listing(s)" in lines
    assert "  target: graph attention networks" in lines
    assert "up to 3 page(s) per listing, 10 records a page" in lines
    assert f"waiting {DEFAULT_MIN_DELAY:g}–{DEFAULT_MAX_DELAY:g}s between page loads" in lines
    assert not [line for line in lines if line.startswith(("warn:", "note:"))]


def test_the_files_a_run_will_touch_are_named_with_create_or_append(tmp_path: Path) -> None:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "results.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "kept-profile").mkdir()
    lines = _explained(["-q", "x", "--bibtex", "out/x.bib", "--profile", "kept-profile"])
    assert "appending to records: out/results.jsonl" in lines
    assert "creating bibtex: out/x.bib" in lines
    assert "creating resume state: out/state.json" in lines
    assert not [line for line in lines if "author profiles" in line]  # no --author was given
    # A directory is reused, not appended to, and the profile is the one thing carried between
    # runs, so a run that inherits cleared cookies says which directory they came from.
    assert "reusing browser profile: kept-profile" in lines
    assert "creating browser profile: fresh" in _explained(["-q", "x", "--profile", "fresh"])


def test_every_path_the_run_writes_is_the_one_it_checks_and_the_one_it_names(tmp_path: Path) -> None:
    # Three questions used to be answered by three lists: what a run touches, what to check
    # before spending a request, and which two flags were pointed at one path.
    from scholar_crawler.cli import _written_paths

    argv = [
        "-q",
        "x",
        "--author",
        "kukA0LcAAAAJ",
        "--bibtex",
        "out/refs.bib",
        "--dump-html",
        "dumps",
    ]
    named = _explained(argv)
    args = build_parser().parse_args(argv)
    checked = {str(path) for _flag, path, _kind in _written_paths(args)}
    for entry in written_paths(
        out=args.out,
        state=args.state,
        challenge_log=args.challenge_log,
        profile=args.profile,
        bibtex=args.bibtex,
        dump_html=args.dump_html,
        authors=True,
    ):
        assert any(str(entry.path) in line and entry.label in line for line in named), entry.label
        assert entry.flag == "" or str(entry.path) in checked, entry.flag


def test_filters_and_expansion_are_spelled_out() -> None:
    lines = _explained(
        [
            "-q",
            "x",
            "--year-from",
            "2020",
            "--sort-by-date",
            "--review-only",
            "--lang",
            "zh-CN",
            "--follow-cites",
            "1",
            "--follow-breadth",
            "3",
        ]
    )
    filters = next(line for line in lines if line.startswith("filters:"))
    assert "years 2020–any" in filters
    assert "sorted by date" in filters
    assert "review articles only" in filters
    assert "interface language zh-CN" in filters
    assert any("following citations 1 level(s) deep, expanding the 3 most-cited" in line for line in lines)


def test_the_takeover_policy_is_described_both_ways() -> None:
    headed = _explained(["-q", "x", "--handoff-timeout", "300", "--max-handoffs", "2"])
    assert any("waiting up to 300s for you to clear it, up to 2 time(s)" in line for line in headed)

    forever = _explained(["-q", "x", "--handoff-timeout", "0"])
    assert any("waiting forever" in line for line in forever)

    headless = _explained(["-q", "x", "--headless"])
    assert any("nothing to hand over without a window" in line for line in headless)


def test_headless_is_warned_about_because_a_challenge_ends_the_run() -> None:
    assert any("--headless cannot hand a challenge" in line for line in _concerns(["-q", "x", "--headless"]))


def test_contradictory_years_are_caught_before_a_request_is_sent() -> None:
    concerns = _concerns(["-q", "x", "--year-from", "2020", "--year-to", "2010"])
    assert any("later than --year-to" in line for line in concerns)


def test_two_outputs_pointed_at_one_file_are_caught() -> None:
    concerns = _concerns(["-q", "x", "--bibtex", "out/results.jsonl"])
    assert any("--bibtex and --out write to the same file" in line for line in concerns)


def test_a_rhythm_faster_than_the_default_is_warned_about() -> None:
    concerns = _concerns(["-q", "x", "--min-delay", "1", "--max-delay", "2"])
    assert any("faster than the default" in line for line in concerns)
    assert not _concerns(["-q", "x", "--min-delay", "8", "--max-delay", "20"])


def test_disabling_the_cooldown_and_the_handoff_budget_are_warned_about() -> None:
    concerns = _concerns(["-q", "x", "--cooldown-every", "0", "--max-handoffs", "0"])
    assert any("--cooldown-every 0" in line for line in concerns)
    assert any("--max-handoffs 0 ends the run at the first challenge" in line for line in concerns)


def test_ignoring_the_takeover_history_is_only_warned_when_there_is_history(tmp_path: Path) -> None:
    assert not [line for line in _concerns(["-q", "x", "--no-learn-from-history"]) if "history" in line]

    log = tmp_path / "out" / "challenges.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        '{"when": "2026-01-01T00:00:00+00:00", "kind": "captcha", "reason": "r", "url": "u", '
        '"outcome": "resolved", "waited": 12.0, "request_count": 3}\n',
        encoding="utf-8",
    )
    concerns = _concerns(["-q", "x", "--no-learn-from-history"])
    assert any("ignores 1 recorded takeover(s)" in line for line in concerns)


def test_resume_without_a_stored_cursor_says_it_starts_over() -> None:
    concerns = _concerns(["-q", "x", "--resume"])
    assert any("holds no cursor for these targets" in line for line in concerns)


def test_a_stored_cursor_without_resume_is_left_to_the_run_to_report(tmp_path: Path) -> None:
    # Every run prints it now, with or without --dry-run, so repeating it here would say the
    # same thing twice in one command.
    args = build_parser().parse_args(["-q", "x", "--state", str(tmp_path / "state.json")])
    listings, _authors = build_targets(args)
    state = StateStore(tmp_path / "state.json")
    state.record(listings[0].signature(), 30)

    concerns = _concerns(["-q", "x", "--state", str(tmp_path / "state.json")])
    assert not any("cursor" in line for line in concerns)


def test_start_loses_to_a_stored_cursor(tmp_path: Path) -> None:
    args = build_parser().parse_args(["-q", "x"])
    listings, _authors = build_targets(args)
    state = StateStore(tmp_path / "out" / "state.json")
    state.load()
    state.record(listings[0].signature(), 30)

    concerns = _concerns(["-q", "x", "--resume", "--start", "50"])
    assert any("--start 50 is ignored" in line and "--resume wins" in line for line in concerns)


def test_bibtex_for_a_profile_states_the_third_page_load() -> None:
    concerns = _concerns(["--author", "kukA0LcAAAAJ", "--bibtex", "out/refs.bib"])
    assert any("three page loads per record" in line for line in concerns)


def test_dumping_pages_and_using_a_proxy_are_stated_as_notes() -> None:
    concerns = _concerns(
        ["-q", "x", "--dump-html", "out/dump", "--proxy", "http://127.0.0.1:8080", "--host", "https://scholar.google.de"]
    )
    assert any("session material" in line for line in concerns)
    assert any("datacenter addresses" in line for line in concerns)
    assert any("not https://scholar.google.com" in line for line in concerns)


def test_warnings_come_before_notes() -> None:
    args = build_parser().parse_args(["-q", "x", "--headless", "--dump-html", "out/dump"])
    listings, authors = build_targets(args)
    from scholar_crawler.cli import _resolve_pacing

    found = concerns_of(args, listings, authors, FollowPolicy(), _resolve_pacing(args))
    levels = [concern.level for concern in found]
    assert levels == sorted(levels, key=lambda level: 0 if level is Level.WARN else 1)


def test_one_mode_reads_the_command_back_and_costs_it(capsys: pytest.CaptureFixture[str]) -> None:
    # Reading a command back and costing it used to be two flags a newcomer had to choose
    # between, and the honest answer was "you want both": neither sends a request, both need
    # targets, and each answers half of "should I start this run?".
    assert main(["-q", "graph attention networks", "-p", "2", "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "[explain] crawling 1 listing(s)" in printed, "what the command means"
    assert "[explain] creating records: out/results.jsonl" in printed, "which files it touches"
    assert "[plan] total:" in printed, "what it would cost"
    assert "[plan] nothing was requested; drop --dry-run to start" in printed
    assert printed.index("[explain]") < printed.index("[plan]"), "meaning first, then cost"
    assert not Path("out").exists()
