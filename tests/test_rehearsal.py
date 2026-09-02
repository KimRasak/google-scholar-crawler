"""The handoff rehearsal: a local page must trip detection and clear on a human action."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.challenge import (  # noqa: E402
    Challenge,
    ChallengeKind,
    ChallengeUnattended,
    HumanHandoff,
    detect_challenge,
)
from scholar_crawler.rehearsal import REHEARSAL_HTML, rehearse  # noqa: E402


@pytest.fixture(scope="module")
def page() -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        yield context.new_page()
        context.close()
        browser.close()


def test_the_rehearsal_page_is_detected_and_clears_on_the_button(page: Page) -> None:
    page.set_content(REHEARSAL_HTML)
    challenge = detect_challenge(page)
    assert challenge is not None
    assert challenge.kind is ChallengeKind.CAPTCHA
    page.click("#rehearsal-clear")
    assert detect_challenge(page) is None


def test_a_full_rehearsal_reports_success(page: Page, capsys: pytest.CaptureFixture[str]) -> None:
    # The stand-in human presses the button as soon as the takeover starts.
    class _Handoff(HumanHandoff):
        def resolve(self, target: Page, challenge: Challenge) -> None:
            target.click("#rehearsal-clear")

    assert rehearse(page, _Handoff()) is True
    printed = capsys.readouterr().out
    assert "detected captcha" in printed
    assert "takeover completed" in printed


def test_a_page_that_never_clears_is_reported(page: Page, capsys: pytest.CaptureFixture[str]) -> None:
    class _Handoff(HumanHandoff):
        def resolve(self, target: Page, challenge: Challenge) -> None:
            return None  # the human walked away, but the wait returned anyway

    assert rehearse(page, _Handoff()) is False
    assert "still looks challenged" in capsys.readouterr().out


def test_headless_refusal_propagates(page: Page) -> None:
    with pytest.raises(ChallengeUnattended, match="rerun without --headless"):
        rehearse(page, HumanHandoff(headless=True))


def test_undetected_rehearsal_page_is_reported(
    page: Page, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("scholar_crawler.rehearsal.detect_challenge", lambda _page: None)
    assert rehearse(page, HumanHandoff()) is False
    assert "not recognised as a challenge" in capsys.readouterr().out
