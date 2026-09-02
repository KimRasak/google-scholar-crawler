"""The crawl loop: paced navigation, challenge handoff, page parsing."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Iterator

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .challenge import RESULTS_SELECTOR, HumanHandoff, detect_challenge
from .models import PageResult, SearchRequest
from .parser import parse_result_page
from .urls import RESULTS_PER_PAGE, search_url


@dataclass(slots=True)
class Pacing:
    """Request rhythm. Slower settings mean fewer challenges, not just politeness.

    :param min_delay: lower bound, in seconds, of the pause before each page request.
    :param max_delay: upper bound, in seconds, of that pause.
    :param cooldown_every: pause longer after this many pages; 0 disables cooldowns.
    :param cooldown_seconds: length of that longer pause.
    :param nav_timeout: per-navigation timeout in seconds.
    """

    min_delay: float = 4.0
    max_delay: float = 11.0
    cooldown_every: int = 10
    cooldown_seconds: float = 90.0
    nav_timeout: float = 45.0

    def sleep_before_request(self, page_index: int) -> None:
        """Sleep the pre-request delay, plus a cooldown at the configured interval.

        :param page_index: zero-based count of pages already fetched in this run.
        """
        if page_index == 0:
            return
        time.sleep(random.uniform(self.min_delay, self.max_delay))
        if self.cooldown_every and page_index % self.cooldown_every == 0:
            print(f"[pace] cooldown {self.cooldown_seconds:.0f}s after {page_index} pages", flush=True)
            time.sleep(self.cooldown_seconds)


class ScholarCrawler:
    """Drives Google Scholar result pages in a human-supervised browser page."""

    def __init__(
        self,
        page: Page,
        handoff: HumanHandoff,
        pacing: Pacing | None = None,
        *,
        host: str = "https://scholar.google.com",
        max_handoffs: int = 5,
    ) -> None:
        """Bind the crawler to one browser page.

        :param page: page used for every navigation.
        :param handoff: human-takeover policy applied when a challenge appears.
        :param pacing: request rhythm; defaults to :class:`Pacing`.
        :param host: Scholar host or regional mirror.
        :param max_handoffs: give up after this many human takeovers in one run.
        """
        self._page = page
        self._handoff = handoff
        self._pacing = pacing or Pacing()
        self._host = host
        self._max_handoffs = max_handoffs
        self.handoff_count = 0

    def fetch_page(self, request: SearchRequest, start: int) -> PageResult:
        """Load one result page, handing over to a human if Scholar challenges us.

        :param request: the query being paginated.
        :param start: result offset to load.
        :returns: the parsed page.
        :raises RuntimeError: when the handoff budget is exhausted or the page never loads.
        """
        url = search_url(request, start=start, host=self._host)
        for attempt in range(1, 4):
            if not self._goto(url, attempt):
                continue
            challenge = detect_challenge(self._page)
            if challenge is not None:
                self.handoff_count += 1
                if self.handoff_count > self._max_handoffs:
                    raise RuntimeError(
                        f"stopping after {self._max_handoffs} human takeovers; "
                        "increase --max-handoffs or slow the crawl down with --min-delay/--max-delay"
                    )
                self._handoff.resolve(self._page, challenge)
                continue
            if self._page.locator(RESULTS_SELECTOR).count() == 0:
                # Zero-hit query, or a layout the selectors miss; both are terminal.
                return PageResult(start=start, results=[], total_estimate=0, has_next=False)
            self._humanize()
            return parse_result_page(self._page.content(), query=request.query, start=start)
        raise RuntimeError(f"could not obtain a result page for offset {start} of {request.query!r}")

    def search(
        self,
        request: SearchRequest,
        *,
        max_pages: int = 3,
        start: int = 0,
    ) -> Iterator[PageResult]:
        """Yield result pages for ``request`` until exhausted or ``max_pages`` reached.

        :param request: the query to run.
        :param max_pages: maximum number of pages to request in this run.
        :param start: first result offset, used for resuming.
        :returns: iterator of parsed pages in Scholar order.
        """
        offset = start
        for page_index in range(max_pages):
            self._pacing.sleep_before_request(page_index)
            page_result = self.fetch_page(request, offset)
            yield page_result
            if not page_result.results or not page_result.has_next:
                return
            offset += RESULTS_PER_PAGE

    def _goto(self, url: str, attempt: int) -> bool:
        """Navigate to ``url``, retrying transient navigation failures.

        :param url: absolute result-page URL.
        :param attempt: 1-based attempt number, used for backoff and the error message.
        :returns: True when the navigation completed; False when this attempt failed and another remains.
        :raises RuntimeError: when the third attempt also fails to navigate.
        """
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=self._pacing.nav_timeout * 1000)
        except (PlaywrightTimeout, PlaywrightError) as error:
            if attempt >= 3:
                raise RuntimeError(f"navigation to {url} failed: {error}") from error
            time.sleep(5.0 * attempt)
            return False
        return True

    def _humanize(self) -> None:
        """Scroll and pause briefly so page dwell time is not machine-uniform."""
        try:
            self._page.mouse.wheel(0, random.randint(300, 1200))
        except PlaywrightError:  # wheel is unavailable on a page that just navigated
            pass
        time.sleep(random.uniform(0.6, 2.2))
