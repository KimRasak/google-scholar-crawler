"""Challenge detection and the human-takeover wait.

Detection runs against a real DOM in headless Chromium (pages are fed through
``set_content``). The takeover wait is driven with a stub page and a scripted
detector, because Playwright's sync API is bound to one thread and a stand-in
"human" cannot drive the real page from another one.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import challenge as challenge_module  # noqa: E402
from scholar_crawler.challenge import (  # noqa: E402
    Challenge,
    ChallengeKind,
    ChallengeUnattended,
    HumanHandoff,
    Takeover,
    detect_challenge,
)
from tests.fixtures import (  # noqa: E402
    CAPTCHA_PAGE_HTML,
    CONSENT_PAGE_HTML,
    EMPTY_PAGE_HTML,
    RESULT_PAGE_HTML,
)

CAPTCHA = Challenge(ChallengeKind.CAPTCHA, "https://scholar.google.com/scholar?q=x", "test")


@pytest.fixture(scope="module")
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        yield context.new_page()
        context.close()
        browser.close()


REAL_BELL = challenge_module._bell
"""The real bell, kept before the autouse fixture below replaces it."""


@pytest.fixture(autouse=True)
def _mute_bell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(challenge_module, "_bell", lambda: None)


class _StubPage:
    """Minimal page surface used by :meth:`HumanHandoff.resolve`."""

    def __init__(self, *, closed: bool = False) -> None:
        self.closed = closed
        self.fronted = 0
        self.url = CAPTCHA.url

    def is_closed(self) -> bool:
        return self.closed

    def bring_to_front(self) -> None:
        self.fronted += 1


def _scripted_detector(
    monkeypatch: pytest.MonkeyPatch, outcomes: list[Challenge | None]
) -> list[Challenge | None]:
    """Replace detection with a fixed sequence and return the consumed log."""
    consumed: list[Challenge | None] = []
    remaining = list(outcomes)

    def detector(_page: object) -> Challenge | None:
        outcome = remaining.pop(0) if remaining else None
        consumed.append(outcome)
        return outcome

    monkeypatch.setattr(challenge_module, "detect_challenge", detector)
    return consumed


def test_result_page_is_not_a_challenge(page: Page) -> None:
    page.set_content(RESULT_PAGE_HTML)
    assert detect_challenge(page) is None


def test_zero_hit_page_is_not_a_challenge(page: Page) -> None:
    page.set_content(EMPTY_PAGE_HTML)
    assert detect_challenge(page) is None


def test_captcha_markup_detected(page: Page) -> None:
    page.set_content(CAPTCHA_PAGE_HTML)
    challenge = detect_challenge(page)
    assert challenge is not None
    assert challenge.kind is ChallengeKind.CAPTCHA
    assert "gs_captcha_ccl" in challenge.detail


def test_consent_wall_detected_by_text(page: Page) -> None:
    page.set_content(CONSENT_PAGE_HTML)
    challenge = detect_challenge(page)
    assert challenge is not None
    assert challenge.kind is ChallengeKind.CONSENT


def test_handoff_waits_then_resumes_when_the_page_is_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    consumed = _scripted_detector(monkeypatch, [CAPTCHA, CAPTCHA, None])
    stub = _StubPage()
    takeover = HumanHandoff(timeout=10.0, poll_interval=0.01).resolve(stub, CAPTCHA)  # type: ignore[arg-type]
    assert consumed == [CAPTCHA, CAPTCHA, None]
    assert stub.fronted == 1
    assert takeover.saw == ("captcha",)
    assert takeover.waited >= 0.0


def test_the_opening_message_says_no_keypress_is_needed_and_how_long_there_is(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _scripted_detector(monkeypatch, [None])
    HumanHandoff(timeout=300.0, poll_interval=0.01).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]
    printed = capsys.readouterr().out
    assert "No keypress needed" in printed
    assert "You have 300s to act" in printed


def test_waiting_without_a_timeout_says_so_instead_of_naming_a_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _scripted_detector(monkeypatch, [None])
    HumanHandoff(timeout=0.0, poll_interval=0.01).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]
    assert "no time limit; it waits as long as needed" in capsys.readouterr().out


def test_a_long_wait_reports_progress_and_how_much_time_is_left(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Silence for ten minutes is the worst thing to leave in front of someone who stepped away.
    _scripted_detector(monkeypatch, [CAPTCHA] * 6 + [None])
    handoff = HumanHandoff(timeout=600.0, poll_interval=0.01, status_every=0.02, warn_before=0.0)
    takeover = handoff.resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]
    status = [line for line in capsys.readouterr().out.splitlines() if "so far" in line]
    assert status, "a long wait must say it is still waiting"
    assert "still showing captcha" in status[0]
    assert "s left" in status[0]
    assert takeover.saw == ("captcha",)


def test_a_challenge_that_turns_into_another_kind_is_reported_and_recorded(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sign_in = Challenge(ChallengeKind.SIGN_IN, CAPTCHA.url, "account sign-in wall")
    _scripted_detector(monkeypatch, [CAPTCHA, sign_in, sign_in, None])
    takeover = HumanHandoff(timeout=60.0, poll_interval=0.01).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]
    printed = capsys.readouterr().out
    assert "the page is now a sign_in: account sign-in wall" in printed
    assert takeover.saw == ("captcha", "sign_in")
    assert takeover.describe() == f"cleared after {takeover.waited:.0f}s (saw captcha -> sign_in)"


def test_the_bell_is_a_bell(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    # Every other test mutes _bell to keep the suite quiet, which left the one line that
    # actually reaches the operator unchecked; REAL_BELL is captured before that muting.
    monkeypatch.setattr(challenge_module.shutil, "which", lambda _name: None)
    REAL_BELL()
    assert capsys.readouterr().err == "\a"


def test_the_run_rings_again_before_it_gives_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rings = []
    monkeypatch.setattr(challenge_module, "_bell", lambda: rings.append(1))
    _scripted_detector(monkeypatch, [CAPTCHA] * 100)
    handoff = HumanHandoff(timeout=0.2, poll_interval=0.01, status_every=10.0, warn_before=10.0)
    with pytest.raises(ChallengeUnattended):
        handoff.resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]
    warnings = [line for line in capsys.readouterr().out.splitlines() if "before the run gives up" in line]
    assert len(warnings) == 1, "warn once, not on every poll"
    assert len(rings) == 2  # the opening bell, then the warning


def test_handoff_times_out_when_nobody_acts(monkeypatch: pytest.MonkeyPatch) -> None:
    _scripted_detector(monkeypatch, [CAPTCHA] * 100)
    with pytest.raises(ChallengeUnattended, match="still present"):
        HumanHandoff(timeout=0.05, poll_interval=0.01).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]


def test_the_timeout_message_names_what_the_window_showed(monkeypatch: pytest.MonkeyPatch) -> None:
    consent = Challenge(ChallengeKind.CONSENT, CAPTCHA.url, "consent wall")
    _scripted_detector(monkeypatch, [CAPTCHA, consent] + [consent] * 100)
    with pytest.raises(ChallengeUnattended, match="showed captcha -> consent"):
        HumanHandoff(timeout=0.05, poll_interval=0.01).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]


def test_a_takeover_summary_reads_as_one_line() -> None:
    assert Takeover(waited=42.4, saw=("captcha",)).describe() == "cleared after 42s (saw captcha)"


def test_handoff_reports_a_closed_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    _scripted_detector(monkeypatch, [CAPTCHA])
    with pytest.raises(ChallengeUnattended, match="closed during handoff"):
        HumanHandoff(timeout=5.0, poll_interval=0.01).resolve(_StubPage(closed=True), CAPTCHA)  # type: ignore[arg-type]


def test_headless_run_refuses_to_wait() -> None:
    with pytest.raises(ChallengeUnattended, match="rerun without --headless"):
        HumanHandoff(headless=True).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]
