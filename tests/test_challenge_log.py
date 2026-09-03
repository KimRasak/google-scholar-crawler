"""The takeover log: what a run writes down when a human has to step in."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import crawler as crawler_module  # noqa: E402
from scholar_crawler.challenge import (  # noqa: E402
    Challenge,
    ChallengeKind,
    ChallengeUnattended,
    HumanHandoff,
    Takeover,
)
from scholar_crawler.cli import main  # noqa: E402
from scholar_crawler.crawler import ScholarCrawler  # noqa: E402
from scholar_crawler.models import SearchRequest  # noqa: E402
from scholar_crawler.rehearsal import REHEARSAL_HTML, rehearse  # noqa: E402
from scholar_crawler.storage import ChallengeLog, ChallengeRecord  # noqa: E402
from scholar_crawler.urls import redact_url  # noqa: E402
from tests.fixtures import CAPTCHA_PAGE_HTML, RESULT_PAGE_HTML  # noqa: E402
from tests.test_crawler import NO_DELAY, _FakePage  # noqa: E402

CAPTCHA = Challenge(
    ChallengeKind.CAPTCHA,
    "https://scholar.google.com/sorry/index?continue=https://x&q=TOKEN12345678&hl=en",
    "matched #gs_captcha_ccl",
)


def _record(**overrides: object) -> ChallengeRecord:
    fields: dict[str, object] = {
        "at": "2026-09-02T12:00:00+00:00",
        "kind": "captcha",
        "url": "https://scholar.google.com/sorry/index?q=REDACTED",
        "reason": "matched #gs_captcha_ccl",
        "request_index": 7,
        "consecutive": 1,
        "waited": 42.0,
        "outcome": "resolved",
        "target": "10",
    }
    fields.update(overrides)
    return ChallengeRecord(**fields)  # type: ignore[arg-type]


def _cleared(_self: object, _page: object, challenge: Challenge) -> Takeover:
    """Stand in for a human who clears the challenge at once.

    :param challenge: the challenge handed over.
    :returns: the summary a real wait returns.
    """
    return Takeover(waited=0.0, saw=(challenge.kind.value,))


def test_a_logged_url_keeps_the_request_and_drops_the_session() -> None:
    search = redact_url("https://scholar.google.com/scholar?q=graph&start=10&scisig=SECRETVALUE&hl=en")
    assert "q=graph" in search
    assert "start=10" in search
    assert "hl=en" in search
    assert "SECRETVALUE" not in search
    assert "scisig=REDACTED" in search

    # On the challenge path ``q`` is the challenge token, not a search query.
    sorry = redact_url(CAPTCHA.url)
    assert "TOKEN12345678" not in sorry
    assert "q=REDACTED" in sorry and "continue=REDACTED" in sorry
    assert "hl=en" in sorry

    assert redact_url("https://scholar.google.com/sorry/index") == (
        "https://scholar.google.com/sorry/index"
    )


def test_a_record_reads_as_one_line() -> None:
    assert _record().describe() == (
        "2026-09-02T12:00:00+00:00  captcha -> resolved, waited 42s "
        "(on request 7, loading 10)"
    )
    assert "x3 in a row" in _record(consecutive=3).describe()
    assert "waited" not in _record(waited=0.2).describe()  # a refusal never waits


def test_the_log_redacts_appends_and_reads_back(tmp_path: Path) -> None:
    log = ChallengeLog(tmp_path / "nested" / "challenges.jsonl")
    first = log.record(
        kind="captcha",
        url=CAPTCHA.url,
        reason="matched #gs_captcha_ccl",
        request_index=3,
        consecutive=1,
        waited=12.34,
        outcome="resolved",
        target="0",
    )
    log.record(
        kind="rate_limit",
        url="https://scholar.google.com/scholar?q=x&scisig=SECRETVALUE",
        reason="unusual traffic",
        request_index=4,
        consecutive=2,
        waited=0.0,
        outcome="unattended",
        target="10",
    )
    assert "TOKEN12345678" not in first.url

    lines = (log.path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["waited"] == 12.3
    assert "SECRETVALUE" not in lines[1]

    log.path.write_text(log.path.read_text(encoding="utf-8") + "not json\n\n", encoding="utf-8")
    entries = log.entries()
    assert [entry.kind for entry in entries] == ["captcha", "rate_limit"]
    assert [entry.outcome for entry in entries] == ["resolved", "unattended"]
    assert entries[1].consecutive == 2


def test_a_missing_log_reads_as_empty(tmp_path: Path) -> None:
    assert ChallengeLog(tmp_path / "absent.jsonl").entries() == []


def test_a_takeover_during_a_crawl_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = iter([CAPTCHA, None])
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: next(outcomes, None))
    monkeypatch.setattr(crawler_module.HumanHandoff, "resolve", _cleared)
    monkeypatch.setattr(crawler_module.time, "sleep", lambda _seconds: None)
    log = ChallengeLog(tmp_path / "challenges.jsonl")
    page = _FakePage(iter([CAPTCHA_PAGE_HTML, RESULT_PAGE_HTML]))
    crawler = ScholarCrawler(
        page,  # type: ignore[arg-type]
        HumanHandoff(timeout=1.0, poll_interval=0.0),
        NO_DELAY,
        challenge_log=log,
    )
    crawler.fetch_page(SearchRequest(query="transformer"), 10)

    entries = log.entries()
    assert len(entries) == 1
    assert (entries[0].kind, entries[0].outcome) == ("captcha", "resolved")
    assert entries[0].consecutive == 1
    assert entries[0].target == "10"
    assert entries[0].request_index == 1  # the challenged load itself
    assert "TOKEN12345678" not in entries[0].url


def test_an_exhausted_takeover_budget_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: CAPTCHA)
    monkeypatch.setattr(crawler_module.HumanHandoff, "resolve", _cleared)
    monkeypatch.setattr(crawler_module.time, "sleep", lambda _seconds: None)
    log = ChallengeLog(tmp_path / "challenges.jsonl")
    crawler = ScholarCrawler(
        _FakePage(iter([CAPTCHA_PAGE_HTML] * 4)),  # type: ignore[arg-type]
        HumanHandoff(timeout=1.0, poll_interval=0.0),
        NO_DELAY,
        max_handoffs=1,
        challenge_log=log,
    )
    with pytest.raises(RuntimeError, match="stopping after 1 human takeovers"):
        crawler.fetch_page(SearchRequest(query="transformer"), 0)

    outcomes = [entry.outcome for entry in log.entries()]
    assert outcomes == ["resolved", "budget"]
    assert log.entries()[1].consecutive == 2


def test_a_headless_refusal_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: CAPTCHA)
    log = ChallengeLog(tmp_path / "challenges.jsonl")
    crawler = ScholarCrawler(
        _FakePage(iter([CAPTCHA_PAGE_HTML])),  # type: ignore[arg-type]
        HumanHandoff(timeout=1.0, headless=True),
        NO_DELAY,
        challenge_log=log,
    )
    with pytest.raises(ChallengeUnattended):
        crawler.fetch_page(SearchRequest(query="transformer"), 0)
    assert [entry.outcome for entry in log.entries()] == ["unattended"]


@pytest.fixture(scope="module")
def browser_page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        yield context.new_page()
        context.close()
        browser.close()


def test_the_kinds_a_wait_observed_survive_a_round_trip(tmp_path: Path) -> None:
    log = ChallengeLog(tmp_path / "challenges.jsonl")
    log.record(
        kind="captcha",
        url="https://scholar.google.com/scholar?q=x",
        reason="matched #gs_captcha_ccl",
        request_index=4,
        consecutive=1,
        waited=42.0,
        outcome="resolved",
        target="page",
        saw=("captcha", "sign_in"),
    )
    entry = log.entries()[0]
    assert entry.saw == ("captcha", "sign_in")
    assert "became sign_in" in entry.describe()


def test_a_record_written_before_the_wait_reported_kinds_still_reads_back(tmp_path: Path) -> None:
    path = tmp_path / "challenges.jsonl"
    path.write_text(
        '{"at": "2026-01-01T00:00:00+00:00", "kind": "captcha", "url": "u", "reason": "r", '
        '"request_index": 1, "consecutive": 1, "waited": 3.0, "outcome": "resolved", '
        '"target": "page"}\n',
        encoding="utf-8",
    )
    entry = ChallengeLog(path).entries()[0]
    assert entry.saw == ()
    assert "became" not in entry.describe()


def test_a_rehearsal_records_the_drill(browser_page: Page, tmp_path: Path) -> None:
    class _Handoff(HumanHandoff):
        def resolve(self, target: Page, challenge: Challenge) -> Takeover:
            target.click("#rehearsal-clear")
            return Takeover(waited=0.0, saw=(challenge.kind.value,))

    browser_page.set_content(REHEARSAL_HTML)
    log = ChallengeLog(tmp_path / "challenges.jsonl")
    assert rehearse(browser_page, _Handoff(), log) is True
    entries = log.entries()
    assert [entry.outcome for entry in entries] == ["rehearsed"]
    assert entries[0].target == "rehearsal"


def test_show_state_reads_the_takeover_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = ChallengeLog(tmp_path / "challenges.jsonl")
    log.record(
        kind="captcha",
        url=CAPTCHA.url,
        reason="matched #gs_captcha_ccl",
        request_index=11,
        consecutive=1,
        waited=95.0,
        outcome="resolved",
        target="20",
    )
    assert main(
        [
            "--show-state",
            "--state",
            str(tmp_path / "state.json"),
            "--challenge-log",
            str(log.path),
        ]
    ) == 0
    printed = capsys.readouterr().out
    assert "1 takeovers" in printed
    assert "captcha x1" in printed
    assert "matched #gs_captcha_ccl at" in printed
    assert "TOKEN12345678" not in printed
