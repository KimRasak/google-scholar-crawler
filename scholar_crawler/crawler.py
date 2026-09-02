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

from .challenge import RESULTS_SELECTOR, Challenge, ChallengeUnattended, HumanHandoff, detect_challenge
from .diagnose import CrawlFailure, diagnose_challenge_loop, diagnose_navigation, diagnose_page
from .models import AuthorPage, AuthorRequest, PageResult, ScholarResult, SearchRequest
from .parser import bibtex_link, parse_author_page, parse_bibtex, parse_result_page
from .storage import ChallengeLog
from .urls import (
    AUTHOR_PAGE_SIZE,
    RESULTS_PER_PAGE,
    absolute,
    author_url,
    cite_url,
    parse_cluster_id,
    search_url,
)

CITE_POPUP_SELECTOR = "a.gs_citi, div.gs_citr, #gs_citi, #gs_cit1"
"""Selectors proving a cite popup rendered: its export links or citation strings."""


@dataclass(slots=True)
class RunStats:
    """What one run cost and how healthy it looked.

    :param requests: page loads issued, including cite popups and BibTeX exports.
    :param handoffs: human takeovers this run.
    :param challenges: takeover count per challenge kind.
    :param navigation_retries: navigations that failed and were retried.
    :param elapsed: wall-clock seconds since the crawler was created.
    :param min_delay: current lower delay bound, after any backoff.
    :param max_delay: current upper delay bound, after any backoff.
    """

    requests: int
    handoffs: int
    challenges: dict[str, int]
    navigation_retries: int
    elapsed: float
    min_delay: float
    max_delay: float

    def render(self) -> str:
        """Format the run as one line.

        The request rate is reported only once the run is long enough for it to mean
        something; over a few seconds it says more about start-up than about pacing.

        :returns: request count and duration, takeovers by kind, retries and the current rhythm.
        """
        minutes = self.elapsed / 60
        plural = "" if self.requests == 1 else "s"
        if minutes >= 0.5:
            spent = f"in {minutes:.1f} min ({self.requests / minutes:.1f}/min)"
        else:
            spent = f"in {self.elapsed:.0f}s"
        kinds = (
            " (" + ", ".join(f"{kind} x{count}" for kind, count in sorted(self.challenges.items())) + ")"
            if self.challenges
            else ""
        )
        return (
            f"{self.requests} request{plural} {spent}, "
            f"{self.handoffs} takeover{'' if self.handoffs == 1 else 's'}{kinds}, "
            f"{self.navigation_retries} navigation retries, "
            f"delay now {self.min_delay:.1f}-{self.max_delay:.1f}s"
        )


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
    :param challenge_cooldown: seconds to wait out before resuming when challenges arrive
        back to back, which means the address is still being watched; 0 disables it.
    """

    min_delay: float = 4.0
    max_delay: float = 11.0
    cooldown_every: int = 10
    cooldown_seconds: float = 90.0
    nav_timeout: float = 45.0
    backoff_factor: float = 1.6
    challenge_cooldown: float = 300.0

    def __post_init__(self) -> None:
        """Reject settings that would silently produce nonsense timing.

        :raises ValueError: on a negative delay, an inverted delay range, or a factor below 1.
        """
        if self.min_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")
        if self.min_delay > self.max_delay:
            raise ValueError(f"min_delay {self.min_delay} exceeds max_delay {self.max_delay}")
        if self.cooldown_every < 0 or self.cooldown_seconds < 0 or self.challenge_cooldown < 0:
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

    def after_handoff(self, consecutive: int = 1) -> None:
        """Slow the crawl down after a human takeover.

        A second challenge with no successful page in between means solving the first one
        did not restore trust, so the run waits out ``challenge_cooldown`` before trying
        again instead of walking straight into the next block.

        :param consecutive: takeovers since the last page that loaded normally.
        """
        if self.backoff_factor != 1.0:
            self.min_delay *= self.backoff_factor
            self.max_delay *= self.backoff_factor
            print(
                f"[pace] backing off to {self.min_delay:.1f}-{self.max_delay:.1f}s between pages",
                flush=True,
            )
        if consecutive > 1 and self.challenge_cooldown:
            wait = self.challenge_cooldown * (consecutive - 1)
            print(
                f"[pace] {consecutive} challenges in a row; waiting {wait:.0f}s before resuming",
                flush=True,
            )
            time.sleep(wait)


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
        challenge_log: ChallengeLog | None = None,
    ) -> None:
        """Bind the crawler to one browser page.

        :param page: page used for every navigation.
        :param handoff: human-takeover policy applied when a challenge appears.
        :param pacing: request rhythm; defaults to :class:`Pacing`.
        :param host: Scholar host or regional mirror.
        :param max_handoffs: give up after this many human takeovers in one run.
        :param dump_dir: when set, every fetched page's HTML is saved here for debugging.
        :param challenge_log: when set, every human takeover is appended to it.
        """
        self._page = page
        self._handoff = handoff
        self._pacing = pacing or Pacing()
        self._host = host
        self._max_handoffs = max_handoffs
        self._dump_dir = dump_dir
        self._challenge_log = challenge_log
        self._started = time.monotonic()
        self.handoff_count = 0
        self.consecutive_handoffs = 0
        self.navigation_retries = 0
        self.challenge_counts: dict[str, int] = {}
        self.request_count = 0
        self.last_status: int | None = None
        self._last_dump: Path | None = None

    def fetch_page(self, request: SearchRequest, start: int) -> PageResult:
        """Load one result page, handing over to a human if Scholar challenges us.

        :param request: the listing being paginated.
        :param start: result offset to load.
        :returns: the parsed page; empty with no successor when the listing has no hits.
        :raises CrawlFailure: when the page never loads or carries no Scholar content.
        :raises RuntimeError: when the handoff budget is exhausted.
        """
        html = self._load(search_url(request, start=start, host=self._host), str(start))
        return parse_result_page(html, query=request.label, start=start)

    def fetch_author_page(self, request: AuthorRequest, cstart: int) -> AuthorPage:
        """Load one batch of an author profile, handing over to a human on a challenge.

        :param request: the profile to read.
        :param cstart: publication offset to load.
        :returns: the parsed profile batch.
        :raises CrawlFailure: when the page never loads or carries no profile header or table.
        :raises RuntimeError: when the handoff budget is exhausted.
        """
        url = author_url(request, cstart=cstart, host=self._host, page_size=AUTHOR_PAGE_SIZE)
        html = self._load(url, f"author-{cstart}")
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
            is truncated to land exactly on the limit and marked ``truncated``, so resume
            state can tell "we stopped" apart from "Scholar ran out". None means no cap.
        :returns: iterator of parsed pages in Scholar order.
        """
        offset = start
        collected = 0
        for _page_index in range(max_pages):
            page_result = self.fetch_page(request, offset)
            if max_results is not None and collected + len(page_result.results) >= max_results:
                page_result.results = page_result.results[: max_results - collected]
                page_result.truncated = True
            collected += len(page_result.results)
            yield page_result
            if page_result.truncated or not page_result.results or not page_result.has_next:
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
            batch is truncated to land exactly on the limit and marked ``truncated``, so
            resume state does not read as finished. None means no cap.
        :returns: iterator of profile batches in profile order.
        """
        offset = cstart
        collected = 0
        for _batch_index in range(max_pages):
            batch = self.fetch_author_page(request, offset)
            if max_results is not None and collected + len(batch.results) >= max_results:
                batch.results = batch.results[: max_results - collected]
                batch.truncated = True
            collected += len(batch.results)
            yield batch
            if batch.truncated or not batch.results or not batch.has_more:
                return
            offset += AUTHOR_PAGE_SIZE

    def fetch_bibtex(self, result: ScholarResult, *, language: str | None = None) -> str | None:
        """Fetch the BibTeX entry for one result: two extra page loads per record.

        The export link is signed by Scholar, so the cite popup must be read before the
        entry itself. Both loads are ordinary navigations in the visible window — Scholar
        answers 429 to requests issued outside the browser's own navigation stack — so
        the pacing and the human takeover cover them like any other page.

        :param result: the record to export. Records parsed from result pages carry
            Scholar's ``data-cid``; profile publications do not, and cost one extra load
            while it is resolved through their cluster listing.
        :param language: interface language (``hl``) for the popup.
        :returns: the BibTeX entry, or None when Scholar exposes none for this record.
        :raises RuntimeError: when the handoff budget is exhausted or a load keeps failing.
        """
        card_id = result.cluster_id or self._resolve_card_id(result, language)
        if not card_id:
            return None
        popup = self._try_load(
            cite_url(card_id, host=self._host, language=language),
            f"cite-{card_id}",
            CITE_POPUP_SELECTOR,
        )
        if popup is None:
            return None
        export_url = absolute(bibtex_link(popup), self._host)
        if export_url is None:
            return None
        body = self._try_load(export_url, f"bib-{card_id}", "pre")
        return parse_bibtex(body) if body is not None else None

    def _resolve_card_id(self, result: ScholarResult, language: str | None) -> str | None:
        """Find Scholar's ``data-cid`` for a record that carries only a numeric cluster id.

        Profile publications link their citing works as ``cites=<cluster id>``; loading that
        cluster's own listing yields a result card, whose ``data-cid`` the cite popup needs.

        :param result: a record whose ``cited_by_url`` holds the cluster id.
        :param language: interface language (``hl``) for the listing.
        :returns: the card id, or None when the record links no cluster or the listing is empty.
        :raises RuntimeError: when the handoff budget is exhausted or the load keeps failing.
        """
        if not result.cited_by_url:
            return None
        try:
            cluster = parse_cluster_id(result.cited_by_url)
        except ValueError:  # a citing-works link Scholar rendered without an id
            return None
        request = SearchRequest(cluster=cluster, language=language)
        html = self._load(search_url(request, host=self._host), f"cluster-{cluster}")
        if html is None:
            return None
        cards = parse_result_page(html, query=request.label).results
        return cards[0].cluster_id if cards else None

    def stats(self) -> RunStats:
        """Snapshot what this run has cost so far.

        :returns: the current run statistics.
        """
        return RunStats(
            requests=self.request_count,
            handoffs=self.handoff_count,
            challenges=dict(self.challenge_counts),
            navigation_retries=self.navigation_retries,
            elapsed=time.monotonic() - self._started,
            min_delay=self._pacing.min_delay,
            max_delay=self._pacing.max_delay,
        )

    def _pace(self) -> None:
        """Apply the pre-request delay for the next request of this run."""
        self._pacing.sleep_before_request(self.request_count)
        self.request_count += 1

    def _load(self, url: str, tag: str, content_selector: str = RESULTS_SELECTOR) -> str:
        """Navigate to ``url`` and return its HTML once no challenge stands in the way.

        A page carrying none of the expected markers stops the run: a zero-hit listing still
        carries Scholar's own "did not match any articles" notice, so a page without any of
        them is a page this tool cannot read, and continuing would report it as no results.

        :param url: absolute Scholar URL.
        :param tag: short label used in dump filenames.
        :param content_selector: selectors proving the loaded page carries content.
        :returns: the page HTML.
        :raises CrawlFailure: when the page cannot be obtained or cannot be understood.
        :raises RuntimeError: when the handoff budget is exhausted.
        """
        html = self._try_load(url, tag, content_selector)
        if html is None:
            raise CrawlFailure(
                diagnose_page(
                    url, status=self.last_status, title=self._title(), dump=self._last_dump
                )
            )
        return html

    def _try_load(self, url: str, tag: str, content_selector: str) -> str | None:
        """Navigate to ``url``, allowing the expected content to be absent.

        Used for pages Scholar legitimately may not have, such as the cite popup of a record
        it exposes no citation export for.

        :param url: absolute Scholar URL.
        :param tag: short label used in dump filenames.
        :param content_selector: selectors proving the loaded page carries content.
        :returns: page HTML, or None when the page carries none of that content.
        :raises CrawlFailure: when the page cannot be obtained at all.
        :raises RuntimeError: when the handoff budget is exhausted.
        """
        for attempt in range(1, 4):
            self._pace()
            if not self._goto(url, attempt):
                continue
            challenge = detect_challenge(self._page)
            if challenge is not None:
                self._hand_over(challenge, tag)
                continue
            if self._page.locator(content_selector).count() == 0:
                self._last_dump = self._dump(f"empty-{tag}")
                return None
            self.consecutive_handoffs = 0
            self._humanize()
            self._dump(f"page-{tag}")
            return self._page.content()
        raise CrawlFailure(diagnose_challenge_loop(url, 3))

    def _hand_over(self, challenge: Challenge, tag: str) -> None:
        """Hand the browser to the human, recording what happened either way.

        A challenge is rare and happens while the human is busy solving it, so the outcome
        is written to the log before it can be lost with the terminal scrollback.

        :param challenge: the detected challenge.
        :param tag: short label of the request that was being loaded.
        :raises RuntimeError: when the takeover budget is exhausted.
        :raises ChallengeUnattended: when no human can act on the challenge.
        """
        self._dump(f"challenge-{challenge.kind.value}-{tag}")
        self.handoff_count += 1
        self.consecutive_handoffs += 1
        kind = challenge.kind.value
        self.challenge_counts[kind] = self.challenge_counts.get(kind, 0) + 1
        started = time.monotonic()
        outcome = "resolved"
        try:
            if self.handoff_count > self._max_handoffs:
                outcome = "budget"
                raise RuntimeError(
                    f"stopping after {self._max_handoffs} human takeovers; "
                    "increase --max-handoffs or slow the crawl down with --min-delay/--max-delay"
                )
            self._handoff.resolve(self._page, challenge)
        except ChallengeUnattended:
            outcome = "unattended"
            raise
        except KeyboardInterrupt:
            outcome = "interrupted"
            raise
        finally:
            self._record_challenge(challenge, tag, time.monotonic() - started, outcome)
        self._pacing.after_handoff(self.consecutive_handoffs)

    def _record_challenge(
        self, challenge: Challenge, tag: str, waited: float, outcome: str
    ) -> None:
        """Append one takeover to the challenge log, when logging is enabled.

        :param challenge: the challenge that was handed over.
        :param tag: short label of the request that was being loaded.
        :param waited: seconds spent waiting for the human.
        :param outcome: how the takeover ended.
        """
        if self._challenge_log is None:
            return
        entry = self._challenge_log.record(
            kind=challenge.kind.value,
            url=challenge.url,
            reason=challenge.detail,
            request_index=self.request_count,
            consecutive=self.consecutive_handoffs,
            waited=waited,
            outcome=outcome,
            target=tag,
        )
        print(f"[handoff] recorded -> {self._challenge_log.path}: {entry.describe()}", flush=True)

    def _goto(self, url: str, attempt: int) -> bool:
        """Navigate to ``url``, retrying transient navigation failures.

        :param url: absolute result-page URL.
        :param attempt: 1-based attempt number, used for backoff and the error message.
        :returns: True when the navigation completed; False when this attempt failed and another remains.
        :raises CrawlFailure: when the third attempt also fails, or when the failure is one no
            retry can get past, such as a refused connection or an unresolvable host.
        """
        try:
            response = self._page.goto(
                url, wait_until="domcontentloaded", timeout=self._pacing.nav_timeout * 1000
            )
        except (PlaywrightTimeout, PlaywrightError) as error:
            diagnosis = diagnose_navigation(error, url)
            if attempt >= 3 or not diagnosis.retry_worthwhile:
                raise CrawlFailure(diagnosis) from error
            self.navigation_retries += 1
            time.sleep(5.0 * attempt)
            return False
        self.last_status = response.status if response is not None else None
        return True

    def _humanize(self) -> None:
        """Scroll and pause briefly so page dwell time is not machine-uniform."""
        with suppress(PlaywrightError):  # the wheel is unavailable on a page that just navigated
            self._page.mouse.wheel(0, random.randint(300, 1200))
        time.sleep(random.uniform(0.6, 2.2))

    def _dump(self, tag: str) -> Path | None:
        """Save the current page HTML under ``dump_dir`` when dumping is enabled.

        :param tag: filename stem describing why the page was saved.
        :returns: the file written, or None when dumping is off or the page was gone.
        """
        if self._dump_dir is None:
            return None
        self._dump_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        try:
            html = self._page.content()
        except PlaywrightError:  # page navigated away before it could be captured
            return None
        path = self._dump_dir / f"{stamp}-{tag}.html"
        path.write_text(html, encoding="utf-8")
        return path

    def _title(self) -> str:
        """Read the current page title for a diagnosis.

        :returns: the title, or an empty string when the page is gone.
        """
        try:
            return self._page.title()
        except PlaywrightError:  # the page closed while the failure was being described
            return ""
