"""Browser session used for crawling.

The session is a *persistent* Chromium/Chrome profile: cookies a human earns by
solving a challenge survive process restarts, which is what keeps the number of
handoffs low. Headed mode is the default because human takeover needs a window.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .challenge import HumanHandoff
from .storage import ChallengeLog
from .urls import SCHOLAR_HOST

_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""


@dataclass(slots=True)
class BrowserOptions:
    """Launch settings for the crawling profile.

    :param user_data_dir: persistent profile directory; reused across runs.
    :param headless: run without a window; disables human takeover.
    :param channel: installed browser channel (``chrome``, ``msedge``) or None for bundled Chromium.
    :param locale: browser locale sent as ``Accept-Language``.
    :param timezone: IANA timezone reported to pages.
    :param proxy_server: optional proxy URL, e.g. ``http://127.0.0.1:8080``.
    :param slow_mo: milliseconds of artificial delay per Playwright action.
    """

    user_data_dir: Path
    headless: bool = False
    channel: str | None = "chrome"
    locale: str = "en-US"
    timezone: str = "America/Los_Angeles"
    proxy_server: str | None = None
    slow_mo: float = 0.0


@contextmanager
def browser_session(options: BrowserOptions) -> Iterator[tuple[BrowserContext, Page]]:
    """Open a persistent browser context and its first page.

    :param options: launch settings.
    :returns: a context manager yielding the context and a ready page; both close on exit.
    """
    options.user_data_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(options.user_data_dir),
            headless=options.headless,
            channel=options.channel,
            locale=options.locale,
            timezone_id=options.timezone,
            slow_mo=options.slow_mo,
            viewport={"width": 1280, "height": 900},
            proxy={"server": options.proxy_server} if options.proxy_server else None,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context.add_init_script(_INIT_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            context.close()


@dataclass(slots=True, frozen=True)
class Session:
    """The browser-backed settings a crawl shares with the modes that replace it.

    :param options: launch settings for the crawling profile.
    :param handoff: takeover policy; its ``headless`` flag decides whether a human can act.
    :param log: takeover log, written by everything that opens a browser.
    :param host: Scholar host or regional mirror.
    :param max_handoffs: give up after this many takeovers.
    :param dump_dir: when set, fetched HTML is saved here.
    :param language: Scholar interface language (``hl``).
    """

    options: BrowserOptions
    handoff: HumanHandoff
    log: ChallengeLog
    host: str = SCHOLAR_HOST
    max_handoffs: int = 5
    dump_dir: Path | None = None
    language: str = "en"
