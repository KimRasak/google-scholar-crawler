"""Crawl-loop behavior: pagination, handoff on challenge, budget enforcement."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler import crawler as crawler_module  # noqa: E402
from scholar_crawler.challenge import Challenge, ChallengeKind, HumanHandoff  # noqa: E402
from scholar_crawler.crawler import Pacing, ScholarCrawler  # noqa: E402
from scholar_crawler.models import SearchRequest  # noqa: E402
from tests.fixtures import CAPTCHA_PAGE_HTML, EMPTY_PAGE_HTML, RESULT_PAGE_HTML  # noqa: E402

CAPTCHA = Challenge(ChallengeKind.CAPTCHA, "https://scholar.google.com/sorry/index", "test")
NO_DELAY = Pacing(min_delay=0.0, max_delay=0.0, cooldown_every=0, nav_timeout=5.0)


class _FakeMouse:
    def wheel(self, _dx: int, _dy: int) -> None:
        return None


class _FakePage:
    """Serves canned HTML per navigation and records the URLs requested."""

    def __init__(self, pages: Iterator[str]) -> None:
        self._pages = pages
        self.html = ""
        self.visited: list[str] = []
        self.mouse = _FakeMouse()
        self.url = ""

    def goto(self, url: str, **_kwargs: object) -> None:
        self.visited.append(url)
        self.url = url
        self.html = next(self._pages)

    def content(self) -> str:
        return self.html

    def is_closed(self) -> bool:
        return False

    def bring_to_front(self) -> None:
        return None

    def locator(self, _selector: str) -> "_FakeLocator":
        return _FakeLocator(self.html)


class _FakeLocator:
    def __init__(self, html: str) -> None:
        self._html = html

    def count(self) -> int:
        return self._html.count('class="gs_r gs_or gs_scl"')


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module.time, "sleep", lambda _seconds: None)


def _crawler(page: _FakePage, **kwargs: object) -> ScholarCrawler:
    handoff = HumanHandoff(timeout=1.0, poll_interval=0.0)
    return ScholarCrawler(page, handoff, NO_DELAY, **kwargs)  # type: ignore[arg-type]


def test_pagination_advances_by_ten_and_stops_on_last_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    page = _FakePage(iter([RESULT_PAGE_HTML, RESULT_PAGE_HTML, RESULT_PAGE_HTML]))
    pages = list(_crawler(page).search(SearchRequest(query="transformer"), max_pages=5))
    # The fixture advertises start=10 only, so page two reports no successor.
    assert [result.start for result in pages] == [0, 10]
    assert [len(result.results) for result in pages] == [3, 3]
    assert "start=10" in page.visited[1]


def test_zero_hit_query_returns_one_empty_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: None)
    page = _FakePage(iter([EMPTY_PAGE_HTML]))
    pages = list(_crawler(page).search(SearchRequest(query="zzzqqq"), max_pages=3))
    assert len(pages) == 1
    assert pages[0].results == []
    assert pages[0].total_estimate == 0


def test_challenge_hands_over_then_refetches(monkeypatch: pytest.MonkeyPatch) -> None:
    outcomes = iter([CAPTCHA, None])
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: next(outcomes, None))
    monkeypatch.setattr(
        crawler_module.HumanHandoff, "resolve", lambda _self, _page, _challenge: None
    )
    page = _FakePage(iter([CAPTCHA_PAGE_HTML, RESULT_PAGE_HTML]))
    crawler = _crawler(page)
    result = crawler.fetch_page(SearchRequest(query="transformer"), 0)
    assert crawler.handoff_count == 1
    assert len(result.results) == 3
    assert page.visited[0] == page.visited[1]


def test_handoff_budget_stops_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawler_module, "detect_challenge", lambda _page: CAPTCHA)
    monkeypatch.setattr(
        crawler_module.HumanHandoff, "resolve", lambda _self, _page, _challenge: None
    )
    page = _FakePage(iter([CAPTCHA_PAGE_HTML] * 10))
    with pytest.raises(RuntimeError, match="1 human takeovers"):
        _crawler(page, max_handoffs=1).fetch_page(SearchRequest(query="transformer"), 0)


def test_cooldown_triggers_on_the_configured_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(crawler_module.time, "sleep", slept.append)
    monkeypatch.setattr(crawler_module.random, "uniform", lambda low, _high: low)
    pacing = Pacing(min_delay=3.0, max_delay=3.0, cooldown_every=2, cooldown_seconds=42.0)
    for index in range(3):
        pacing.sleep_before_request(index)
    assert slept == [3.0, 3.0, 42.0]
