"""The modes that replace a crawl, driven without argument parsing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.browser import BrowserOptions, Session  # noqa: E402
from scholar_crawler.challenge import HumanHandoff  # noqa: E402
from scholar_crawler.modes import (  # noqa: E402
    forget_state,
    rehearse_takeover,
    show_state,
    show_takeovers,
)
from scholar_crawler.storage import ChallengeLog, StateStore  # noqa: E402


def _session(tmp_path: Path, *, headless: bool = True) -> Session:
    return Session(
        options=BrowserOptions(user_data_dir=tmp_path / "profile", headless=headless, channel=None),
        handoff=HumanHandoff(timeout=1.0, poll_interval=0.0, headless=headless),
        log=ChallengeLog(tmp_path / "challenges.jsonl"),
        dump_dir=None,
    )


def test_a_rehearsal_needs_no_argument_parser(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The point of the split: a mode runs from plain values, in a real browser.
    assert rehearse_takeover(_session(tmp_path)) == 0  # headless refusal is the designed outcome
    printed = capsys.readouterr().out
    assert "no request is sent to Google" in printed
    assert "refused without a window, as designed" in printed
    assert [entry.outcome for entry in ChallengeLog(tmp_path / "challenges.jsonl").entries()] == [
        "unattended"
    ]


def test_state_is_shown_and_forgotten_from_paths_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "challenges.jsonl"
    store = StateStore(state_path)
    store.record("attention [en]", 30)
    store.record("author:abc [en]", 100, exhausted=True)

    assert show_state(state_path, log_path) == 0
    printed = capsys.readouterr().out
    assert "2 targets" in printed and "(1 finished)" in printed
    assert "[handoff]" not in printed  # no log yet, so nothing to say

    assert forget_state(state_path, "attention") == 0
    printed = capsys.readouterr().out
    assert "dropped 1 target" in printed
    reread = StateStore(state_path)
    reread.load()
    assert [entry.signature for entry in reread.entries()] == ["author:abc [en]"]

    assert forget_state(state_path, "nothing-matches-this") == 0
    assert "no stored target matches" in capsys.readouterr().out


def test_takeovers_are_printed_newest_last(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = ChallengeLog(tmp_path / "challenges.jsonl")
    for index in range(7):
        log.record(
            kind="captcha" if index % 2 else "rate_limit",
            url=f"https://scholar.google.com/scholar?q=x&start={index}0",
            reason="matched #gs_captcha_ccl",
            request_index=index,
            consecutive=1,
            waited=5.0,
            outcome="resolved",
            target=str(index),
        )
    show_takeovers(log.path, limit=2)
    printed = capsys.readouterr().out
    assert "7 takeovers" in printed
    assert "captcha x3, rate_limit x4" in printed
    assert printed.count("-> resolved") == 2
    assert "loading 6" in printed and "loading 0" not in printed
