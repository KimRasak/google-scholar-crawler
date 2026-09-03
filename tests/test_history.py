"""Learning a starting rhythm from the takeovers previous runs recorded."""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.cli import DEFAULT_MAX_DELAY, DEFAULT_MIN_DELAY, main  # noqa: E402
from scholar_crawler.history import (  # noqa: E402
    MAX_FACTOR,
    History,
    advise,
    read_history,
    suggest_factor,
)
from scholar_crawler.storage import ChallengeLog, ChallengeRecord  # noqa: E402


def _block(
    request_index: int = 20, *, consecutive: int = 1, kind: str = "captcha", outcome: str = "resolved"
) -> ChallengeRecord:
    return ChallengeRecord(
        at=f"2026-09-0{min(request_index % 9 + 1, 9)}T10:00:00+00:00",
        kind=kind,
        url="https://scholar.google.com/sorry/index?q=REDACTED",
        reason="matched #gs_captcha_ccl",
        request_index=request_index,
        consecutive=consecutive,
        waited=30.0,
        outcome=outcome,
        target="0",
    )


def test_rehearsals_are_not_evidence_of_being_blocked() -> None:
    history = read_history([_block(outcome="rehearsed"), _block(outcome="rehearsed")])
    assert history.blocks == 0
    assert history.typical_request is None
    assert advise([_block(outcome="rehearsed")], 4.0, 11.0) is None
    assert advise([], 4.0, 11.0) is None


def test_history_summarizes_kinds_position_and_streaks() -> None:
    history = read_history(
        [
            _block(10),
            _block(20, consecutive=2),
            _block(60, kind="rate_limit"),
            _block(0, outcome="rehearsed"),
        ]
    )
    assert history.blocks == 3
    assert history.back_to_back == 1
    assert history.typical_request == 20
    assert history.kinds == {"captcha": 2, "rate_limit": 1}
    assert history.last_at == _block(60).at
    described = history.describe()
    assert "3 previous blocks" in described
    assert "captcha x2, rate_limit x1" in described
    assert "typically at request 20" in described
    assert "1 arrived back to back" in described


def test_one_block_is_a_warning_not_a_pattern() -> None:
    history = read_history([_block(50)])
    assert suggest_factor(history) == 1.0
    advice = advise([_block(50)], 4.0, 11.0)
    assert advice is not None
    assert advice.changes_pacing is False
    assert (advice.min_delay, advice.max_delay) == (4.0, 11.0)
    assert "keeping the current rhythm" in advice.describe()


def test_repeated_blocks_widen_the_rhythm() -> None:
    late = [_block(80), _block(90)]
    assert suggest_factor(read_history(late)) == pytest.approx(1.3)

    early = [_block(10), _block(20)]
    assert suggest_factor(read_history(early)) == pytest.approx(1.5)  # early means the rhythm

    streak = [_block(10), _block(12, consecutive=2)]
    assert suggest_factor(read_history(streak)) == pytest.approx(1.7)

    persistent = [_block(5, consecutive=2) for _ in range(6)]
    assert suggest_factor(read_history(persistent)) == MAX_FACTOR


def test_the_widest_advice_is_the_sum_of_its_reasons() -> None:
    # MAX_FACTOR is a statement about the terms, not a clamp: every combination of them must
    # land on or below it, or the ceiling documented in the README would be fiction.
    for blocks, back_to_back, typical in product((0, 1, 2, 4, 5, 40), (0, 1, 9), (1, 30, 31, 900)):
        history = History(
            blocks=blocks,
            back_to_back=back_to_back,
            typical_request=typical,
            last_at="2026-09-03T00:00:00+00:00",
            kinds={},
        )
        assert 1.0 <= suggest_factor(history) <= MAX_FACTOR


def test_advice_never_speeds_a_run_up() -> None:
    advice = advise([_block(10), _block(12, consecutive=2)], 4.0, 11.0)
    assert advice is not None
    assert advice.min_delay > 4.0 and advice.max_delay > 11.0
    assert (advice.min_delay, advice.max_delay) == (6.8, 18.7)
    assert "x1.7" in advice.describe()


def _log_with(path: Path, *records: ChallengeRecord) -> Path:
    log = ChallengeLog(path)
    for record in records:
        log.record(
            kind=record.kind,
            url=record.url,
            reason=record.reason,
            request_index=record.request_index,
            consecutive=record.consecutive,
            waited=record.waited,
            outcome=record.outcome,
            target=record.target,
        )
    return path


def _dry_run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    assert main([*argv, "--dry-run"]) == 0
    return capsys.readouterr().out


def test_a_run_starts_slower_after_previous_blocks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _log_with(tmp_path / "challenges.jsonl", _block(10), _block(12, consecutive=2))
    printed = _dry_run(["-q", "x", "--challenge-log", str(log)], capsys)
    assert "2 previous blocks" in printed
    assert "starting at 6.8-18.7s (x1.7)" in printed
    assert "at 7-19s between requests" in printed


def test_delays_passed_by_hand_are_never_widened(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = _log_with(tmp_path / "challenges.jsonl", _block(10), _block(12, consecutive=2))
    printed = _dry_run(
        ["-q", "x", "--challenge-log", str(log), "--min-delay", "8", "--max-delay", "20"], capsys
    )
    assert "2 previous blocks" in printed
    assert "keeping the delays you passed" in printed
    assert "at 8-20s between requests" in printed
    assert "starting at" not in printed


def test_learning_can_be_turned_off(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = _log_with(tmp_path / "challenges.jsonl", _block(10), _block(12, consecutive=2))
    printed = _dry_run(
        ["-q", "x", "--challenge-log", str(log), "--no-learn-from-history"], capsys
    )
    assert "previous blocks" not in printed
    assert f"at {DEFAULT_MIN_DELAY:.0f}-{DEFAULT_MAX_DELAY:.0f}s between requests" in printed


def test_an_empty_log_leaves_the_defaults_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    printed = _dry_run(["-q", "x", "--challenge-log", str(tmp_path / "absent.jsonl")], capsys)
    assert "[pace]" not in printed
    assert "at 4-11s between requests" in printed
