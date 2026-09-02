"""Challenge detection and the human-takeover wait.

Detection runs against a real DOM in headless Chromium (pages are fed through
``set_content``). The takeover wait is driven with a stub page and a scripted
detector, because Playwright's sync API is bound to one thread and a stand-in
"human" cannot drive the real page from another one.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import challenge as challenge_module  # noqa: E402
from scholar_crawler.challenge import (  # noqa: E402
    Challenge,
    ChallengeKind,
    ChallengeUnattended,
    HumanHandoff,
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
    HumanHandoff(timeout=10.0, poll_interval=0.01).resolve(stub, CAPTCHA)  # type: ignore[arg-type]
    assert consumed == [CAPTCHA, CAPTCHA, None]
    assert stub.fronted == 1


def test_handoff_times_out_when_nobody_acts(monkeypatch: pytest.MonkeyPatch) -> None:
    _scripted_detector(monkeypatch, [CAPTCHA] * 100)
    with pytest.raises(ChallengeUnattended, match="still present"):
        HumanHandoff(timeout=0.05, poll_interval=0.01).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]


def test_handoff_reports_a_closed_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    _scripted_detector(monkeypatch, [CAPTCHA])
    with pytest.raises(ChallengeUnattended, match="closed during handoff"):
        HumanHandoff(timeout=5.0, poll_interval=0.01).resolve(_StubPage(closed=True), CAPTCHA)  # type: ignore[arg-type]


def test_headless_run_refuses_to_wait() -> None:
    with pytest.raises(ChallengeUnattended, match="rerun without --headless"):
        HumanHandoff(headless=True).resolve(_StubPage(), CAPTCHA)  # type: ignore[arg-type]
