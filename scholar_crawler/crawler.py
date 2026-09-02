"""The crawl loop: paced navigation, challenge handoff, page parsing."""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from .challenge import RESULTS_SELECTOR, HumanHandoff, detect_challenge
from .models import AuthorPage, AuthorRequest, PageResult, ScholarResult, SearchRequest
from .parser import bibtex_link, parse_author_page, parse_bibtex, parse_result_page
from .urls import (
    AUTHOR_PAGE_SIZE,
    RESULTS_PER_PAGE,
    absolute,
    author_url,
    cite_url,
    search_url,
)

CITE_POPUP_SELECTOR = "a.gs_citi, div.gs_citr, #gs_citi, #gs_cit1"
"""Selectors proving a cite popup rendered: its export links or citation strings."""


@dataclass(slots=True)
class Pacing:
    """Request rhythm. Slower settings mean fewer challenges, not just politeness.

    :param min_delay: lower bound, in seconds, of the pause before each page request.
    :param max_delay: upper bound, in seconds, of that pause.
    :param cooldown_every: pause longer after this many pages; 0 disables cooldowns.
    :param cooldown_seconds: length of that longer pause.
    :param nav_timeout: per-navigation timeout in seconds.
    :param backoff_factor: multiplier applied to both delay bounds after each human
        takeover, so a challenged run automatically slows down; 1.0 keeps the rhythm.
    """

    min_delay: float = 4.0
    max_delay: float = 11.0
    cooldown_every: int = 10
    cooldown_seconds: float = 90.0
    nav_timeout: float = 45.0
    backoff_factor: float = 1.6

    def __post_init__(self) -> None:
        """Reject settings that would silently produce nonsense timing.

        :raises ValueError: on a negative delay, an inverted delay range, or a factor below 1.
        """
        if self.min_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")
        if self.min_delay > self.max_delay:
            raise ValueError(f"min_delay {self.min_delay} exceeds max_delay {self.max_delay}")
        if self.cooldown_every < 0 or self.cooldown_seconds < 0:
            raise ValueError("cooldown settings must not be negative")
        if self.backoff_factor < 1.0:
            raise ValueError("backoff_factor must be >= 1.0 so takeovers never speed the crawl up")

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

    def after_handoff(self) -> None:
        """Widen both delay bounds by ``backoff_factor`` after a human takeover."""
        if self.backoff_factor == 1.0:
            return
        self.min_delay *= self.backoff_factor
        self.max_delay *= self.backoff_factor
        print(
            f"[pace] backing off to {self.min_delay:.1f}-{self.max_delay:.1f}s between pages",
            flush=True,
        )


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
        dump_dir: Path | None = None,
    ) -> None:
        """Bind the crawler to one browser page.

        :param page: page used for every navigation.
        :param handoff: human-takeover policy applied when a challenge appears.
        :param pacing: request rhythm; defaults to :class:`Pacing`.
        :param host: Scholar host or regional mirror.
        :param max_handoffs: give up after this many human takeovers in one run.
        :param dump_dir: when set, every fetched page's HTML is saved here for debugging.
        """
        self._page = page
        self._handoff = handoff
        self._pacing = pacing or Pacing()
        self._host = host
        self._max_handoffs = max_handoffs
        self._dump_dir = dump_dir
        self.handoff_count = 0
        self.request_count = 0

    def fetch_page(self, request: SearchRequest, start: int) -> PageResult:
        """Load one result page, handing over to a human if Scholar challenges us.

        :param request: the listing being paginated.
        :param start: result offset to load.
        :returns: the parsed page; empty with no successor when the listing has no hits.
        :raises RuntimeError: when the handoff budget is exhausted or the page never loads.
        """
        html = self._load(search_url(request, start=start, host=self._host), str(start))
        if html is None:
            return PageResult(start=start, results=[], total_estimate=0, has_next=False)
        return parse_result_page(html, query=request.label, start=start)

    def fetch_author_page(self, request: AuthorRequest, cstart: int) -> AuthorPage:
        """Load one batch of an author profile, handing over to a human on a challenge.

        :param request: the profile to read.
        :param cstart: publication offset to load.
        :returns: the parsed profile batch.
        :raises RuntimeError: when the profile carries no recognizable header or table,
            the handoff budget is exhausted, or the page never loads.
        """
        url = author_url(request, cstart=cstart, host=self._host, page_size=AUTHOR_PAGE_SIZE)
        html = self._load(url, f"author-{cstart}")
        if html is None:
            raise RuntimeError(
                f"no profile header or publication table at {url}; "
                "check the profile id, or inspect the page with --dump-html"
            )
        return parse_author_page(html, user_id=request.user_id, cstart=cstart)

    def search(
        self,
        request: SearchRequest,
        *,
        max_pages: int = 3,
        start: int = 0,
        max_results: int | None = None,
    ) -> Iterator[PageResult]:
        """Yield result pages for ``request`` until exhausted or a limit is reached.

        :param request: the listing to page through.
        :param max_pages: maximum number of pages to request in this run.
        :param start: first result offset, used for resuming.
        :param max_results: stop once this many results have been yielded; the last page
            is truncated to land exactly on the limit. None means no result cap.
        :returns: iterator of parsed pages in Scholar order.
        """
        offset = start
        collected = 0
        for _page_index in range(max_pages):
            page_result = self.fetch_page(request, offset)
            if max_results is not None and collected + len(page_result.results) >= max_results:
                page_result.results = page_result.results[: max_results - collected]
                page_result.has_next = False
            collected += len(page_result.results)
            yield page_result
            if not page_result.results or not page_result.has_next:
                return
            offset += RESULTS_PER_PAGE

    def crawl_author(
        self,
        request: AuthorRequest,
        *,
        max_pages: int = 3,
        cstart: int = 0,
        max_results: int | None = None,
    ) -> Iterator[AuthorPage]:
        """Yield an author's publications in batches of :data:`AUTHOR_PAGE_SIZE`.

        :param request: the profile to read.
        :param max_pages: maximum number of batches to request in this run.
        :param cstart: first publication offset, used for resuming.
        :param max_results: stop once this many publications have been yielded; the last
            batch is truncated to land exactly on the limit. None means no cap.
        :returns: iterator of profile batches in profile order.
        """
        offset = cstart
        collected = 0
        for _batch_index in range(max_pages):
            batch = self.fetch_author_page(request, offset)
            if max_results is not None and collected + len(batch.results) >= max_results:
                batch.results = batch.results[: max_results - collected]
                batch.has_more = False
            collected += len(batch.results)
            yield batch
            if not batch.results or not batch.has_more:
                return
            offset += AUTHOR_PAGE_SIZE

    def fetch_bibtex(self, result: ScholarResult, *, language: str | None = None) -> str | None:
        """Fetch the BibTeX entry for one result: two extra page loads per record.

        The export link is signed by Scholar, so the cite popup must be read before the
        entry itself. Both loads are ordinary navigations in the visible window — Scholar
        answers 429 to requests issued outside the browser's own navigation stack — so
        the pacing and the human takeover cover them like any other page.

        :param result: a record carrying Scholar's ``data-cid``; author-profile records
            have none, and get no BibTeX.
        :param language: interface language (``hl``) for the popup.
        :returns: the BibTeX entry, or None when Scholar exposes none for this record.
        :raises RuntimeError: when the handoff budget is exhausted or a load keeps failing.
        """
        if not result.cluster_id:
            return None
        popup = self._load(
            cite_url(result.cluster_id, host=self._host, language=language),
            f"cite-{result.cluster_id}",
            content_selector=CITE_POPUP_SELECTOR,
        )
        if popup is None:
            return None
        export_url = absolute(bibtex_link(popup), self._host)
        if export_url is None:
            return None
        body = self._load(export_url, f"bib-{result.cluster_id}", content_selector="pre")
        return parse_bibtex(body) if body is not None else None

    def _pace(self) -> None:
        """Apply the pre-request delay for the next request of this run."""
        self._pacing.sleep_before_request(self.request_count)
        self.request_count += 1

    def _load(
        self, url: str, tag: str, content_selector: str = RESULTS_SELECTOR
    ) -> str | None:
        """Navigate to ``url`` and return its HTML once no challenge stands in the way.

        :param url: absolute Scholar URL.
        :param tag: short label used in dump filenames.
        :param content_selector: selectors proving the loaded page carries content.
        :returns: page HTML, or None when the page carries no Scholar content.
        :raises RuntimeError: when the handoff budget is exhausted or navigation keeps failing.
        """
        for attempt in range(1, 4):
            self._pace()
            if not self._goto(url, attempt):
                continue
            challenge = detect_challenge(self._page)
            if challenge is not None:
                self._dump(f"challenge-{challenge.kind.value}-{tag}")
                self.handoff_count += 1
                if self.handoff_count > self._max_handoffs:
                    raise RuntimeError(
                        f"stopping after {self._max_handoffs} human takeovers; "
                        "increase --max-handoffs or slow the crawl down with --min-delay/--max-delay"
                    )
                self._handoff.resolve(self._page, challenge)
                self._pacing.after_handoff()
                continue
            if self._page.locator(content_selector).count() == 0:
                # Zero-hit listing, or a layout the selectors miss; both are terminal.
                self._dump(f"empty-{tag}")
                return None
            self._humanize()
            self._dump(f"page-{tag}")
            return self._page.content()
        raise RuntimeError(f"could not obtain {url} after three attempts")

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
        with suppress(PlaywrightError):  # the wheel is unavailable on a page that just navigated
            self._page.mouse.wheel(0, random.randint(300, 1200))
        time.sleep(random.uniform(0.6, 2.2))

    def _dump(self, tag: str) -> None:
        """Save the current page HTML under ``dump_dir`` when dumping is enabled.

        :param tag: filename stem describing why the page was saved.
        """
        if self._dump_dir is None:
            return
        self._dump_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        try:
            html = self._page.content()
        except PlaywrightError:  # page navigated away before it could be captured
            return
        (self._dump_dir / f"{stamp}-{tag}.html").write_text(html, encoding="utf-8")
