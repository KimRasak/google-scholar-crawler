"""The --keep-background watcher: who had the screen, and getting it back.

The screen is read through osascript, which the tests replace with scripted
answers, so they run on any platform and never touch the real one. The session
wiring runs against a real headless browser, which cannot take a foreground
anyway — exactly the case where nothing may be watched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import focus  # noqa: E402
from scholar_crawler.browser import BrowserOptions, browser_session  # noqa: E402


@pytest.fixture(autouse=True)
def _on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real decision needs darwin; the scripted screen stands in for osascript either way.
    monkeypatch.setattr(sys, "platform", "darwin")


def _scripted_screen(
    monkeypatch: pytest.MonkeyPatch, front: list[str | None]
) -> list[str]:
    """Answer :func:`focus.frontmost_app` from ``front``, then repeat its last value.

    :returns: the list :func:`focus.activate` records its requests in.
    """
    activated: list[str] = []

    def screen() -> str | None:
        if len(front) > 1:
            return front.pop(0)
        return front[0]

    monkeypatch.setattr(focus, "frontmost_app", screen)
    monkeypatch.setattr(focus, "activate", lambda app: activated.append(app) or True)
    return activated


def test_the_channel_maps_to_its_application_name() -> None:
    assert focus.app_name("chrome") == "Google Chrome"
    assert focus.app_name("chrome-canary") == "Google Chrome Canary"
    assert focus.app_name("msedge") == "Microsoft Edge"
    # No channel means the bundled browser, and an unknown channel falls back to it.
    assert focus.app_name(None) == "Chromium"
    assert focus.app_name("not-a-channel") == "Chromium"


def test_the_screen_is_only_read_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(focus, "_osascript", lambda _script: "iTerm2")
    monkeypatch.setattr(sys, "platform", "linux")
    assert focus.frontmost_app() is None
    assert focus.activate("Google Chrome") is False
    monkeypatch.setattr(sys, "platform", "darwin")
    assert focus.frontmost_app() == "iTerm2"


def test_a_refused_applescript_reads_as_no_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(*_args: object, **_kwargs: object) -> None:
        raise OSError("not allowed to send Apple events")

    monkeypatch.setattr(focus.subprocess, "run", refused)
    assert focus.frontmost_app() is None
    assert focus.activate("Google Chrome") is False


def test_remember_skips_runs_that_may_take_the_foreground() -> None:
    assert focus.remember(steal_focus=True, headless=False) is None
    # A headless run has no window, so there is no screen to hand back either.
    assert focus.remember(steal_focus=False, headless=True) is None


def test_remember_notes_who_has_the_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(focus, "frontmost_app", lambda: "iTerm2")
    assert focus.remember(steal_focus=False, headless=False) == "iTerm2"


def test_an_unanswerable_screen_says_the_flag_cannot_act(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The flag was asked for and macOS said no: silence would look like the flag did
    # something, when the window will come forward exactly as before.
    monkeypatch.setattr(focus, "frontmost_app", lambda: None)
    assert focus.remember(steal_focus=False, headless=False) is None
    printed = capsys.readouterr().err
    assert "--keep-background cannot act" in printed
    assert printed.count("cannot read which app is frontmost") == 1


def test_keep_behind_starts_nothing_without_a_previous_app() -> None:
    assert focus.keep_behind(None, "Google Chrome") is None


def test_the_screen_is_handed_back_when_the_browser_takes_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    activated = _scripted_screen(monkeypatch, ["Google Chrome", "iTerm2"])
    focus._hand_back("iTerm2", "Google Chrome")
    assert activated == ["iTerm2"]
    printed = capsys.readouterr().err
    assert "keeping Google Chrome behind your work" in printed
    assert "cannot read which app is frontmost" not in printed


def test_focus_that_was_never_stolen_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    activated = _scripted_screen(monkeypatch, ["iTerm2"])
    focus._hand_back("iTerm2", "Google Chrome")
    assert activated == []
    assert "behind your work" in capsys.readouterr().err


def test_an_unreadable_screen_is_reported_once_and_left_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    activated = _scripted_screen(monkeypatch, [None])
    focus._hand_back("iTerm2", "Google Chrome")
    assert activated == [], "pushing blind would fight the operator"
    printed = capsys.readouterr().err
    assert "cannot read which app is frontmost" in printed
    assert printed.count("cannot read") == 1, "say it once, then stop"


def test_the_hand_back_gives_up_after_a_few_tries(monkeypatch: pytest.MonkeyPatch) -> None:
    # The browser stays frontmost no matter what; the watcher may not push forever.
    activated = _scripted_screen(monkeypatch, ["Google Chrome"])
    monkeypatch.setattr(focus, "BUDGET_SECONDS", 0.1)
    monkeypatch.setattr(focus, "POLL_SECONDS", 0.01)
    focus._hand_back("iTerm2", "Google Chrome")
    assert len(activated) == focus.GIVE_BACKS


def test_the_session_asks_who_had_the_screen_and_starts_the_watcher(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The wiring, not the screen: a headless browser is used because it cannot take a
    # foreground, and the screen-reading itself is scripted away.
    asked: dict[str, bool] = {}
    started: list[tuple[str, str]] = []
    monkeypatch.setattr(
        focus,
        "remember",
        lambda *, steal_focus, headless: asked.update(steal_focus=steal_focus, headless=headless)
        or "iTerm2",
    )
    monkeypatch.setattr(
        focus, "keep_behind", lambda previous, browser: started.append((previous, browser))
    )
    options = BrowserOptions(
        user_data_dir=tmp_path / "profile", headless=True, channel=None, steal_focus=False
    )
    with browser_session(options) as (_context, _page):
        pass
    assert asked == {"steal_focus": False, "headless": True}
    assert started == [("iTerm2", "Chromium")]


def test_a_run_that_may_take_the_foreground_asks_for_nothing_and_watches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked: dict[str, bool] = {}
    started: list[tuple[str, str]] = []
    monkeypatch.setattr(
        focus,
        "remember",
        lambda *, steal_focus, headless: asked.update(steal_focus=steal_focus, headless=headless),
    )
    monkeypatch.setattr(
        focus, "keep_behind", lambda previous, browser: started.append((previous, browser))
    )
    # steal_focus spelled out because the BrowserOptions default is --keep-background's False.
    options = BrowserOptions(
        user_data_dir=tmp_path / "profile", headless=True, channel=None, steal_focus=True
    )
    with browser_session(options) as (_context, _page):
        pass
    assert asked == {"steal_focus": True, "headless": True}
    # What remember returned flows into keep_behind unchanged, and None watches nothing.
    assert started == [(None, "Chromium")]
