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
from playwright.sync_api import Error as PlaywrightError

from .challenge import HumanHandoff
from .diagnose import CrawlFailure, diagnose_launch, diagnose_unwritable
from .storage import ChallengeLog, unwritable
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
    """

    user_data_dir: Path
    headless: bool = False
    channel: str | None = "chrome"
    locale: str = "en-US"
    timezone: str = "America/Los_Angeles"
    proxy_server: str | None = None


@contextmanager
def browser_session(options: BrowserOptions) -> Iterator[tuple[BrowserContext, Page]]:
    """Open a persistent browser context and its first page.

    :param options: launch settings.
    :returns: a context manager yielding the context and a ready page; both close on exit.
    """
    unusable = unwritable(options.user_data_dir, kind="dir")
    if unusable:
        raise CrawlFailure(diagnose_unwritable(unusable, "--profile", kind="dir"))
    options.user_data_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(options.user_data_dir),
                headless=options.headless,
                channel=options.channel,
                locale=options.locale,
                timezone_id=options.timezone,
                viewport={"width": 1280, "height": 900},
                proxy={"server": options.proxy_server} if options.proxy_server else None,
                args=["--disable-blink-features=AutomationControlled"],
            )
        except PlaywrightError as error:
            # No window means no request at all, so this ends the run with the local cause
            # rather than a stack trace from inside Playwright.
            raise CrawlFailure(
                diagnose_launch(error, channel=options.channel, profile=options.user_data_dir)
            ) from error
        context.add_init_script(_INIT_SCRIPT)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            context.close()


TIMEZONES = {
    "en": "America/Los_Angeles",
    "de": "Europe/Berlin",
    "fr": "Europe/Paris",
    "es": "Europe/Madrid",
    "it": "Europe/Rome",
    "nl": "Europe/Amsterdam",
    "pl": "Europe/Warsaw",
    "pt": "America/Sao_Paulo",
    "ru": "Europe/Moscow",
    "tr": "Europe/Istanbul",
    "ja": "Asia/Tokyo",
    "ko": "Asia/Seoul",
    "zh": "Asia/Shanghai",
    "id": "Asia/Jakarta",
    "hi": "Asia/Kolkata",
    "ar": "Asia/Dubai",
    "he": "Asia/Jerusalem",
    "th": "Asia/Bangkok",
    "vi": "Asia/Ho_Chi_Minh",
    "zh-TW": "Asia/Taipei",
    "zh-HK": "Asia/Hong_Kong",
    "pt-PT": "Europe/Lisbon",
    "en-GB": "Europe/London",
}
"""A plausible timezone for each interface language Scholar serves.

One of many, not the only one: the point is only that the zone and the language agree. A window
asking Scholar for German pages from US Pacific time is as odd a browser as one asking for
German pages while sending ``Accept-Language: en-US``.
"""

DEFAULT_TIMEZONE = "UTC"
"""Zone for a language this table does not name.

Spelled the way Chromium reports it back, so what the window is asked to claim is exactly what
a page reads from it — ``Etc/UTC`` is the same zone but comes back as ``UTC``.
"""


def timezone_for(language: str) -> str:
    """Choose the browser timezone that matches the Scholar interface language.

    A regional tag is looked up whole before its base language, so ``zh-TW`` is read from Taipei
    rather than from Shanghai.

    :param language: the ``--lang`` value, which becomes Scholar's ``hl``.
    :returns: a timezone consistent with that language; ``--timezone`` overrides it.
    """
    tag = language.strip()
    base = tag.split("-")[0].lower()
    regional = f"{base}-{tag.split('-')[1].upper()}" if "-" in tag else tag
    return TIMEZONES.get(regional) or TIMEZONES.get(base, DEFAULT_TIMEZONE)


def locale_for(language: str) -> str:
    """Choose the browser locale that matches the Scholar interface language.

    The interface language and the browser locale are one fact: a window asking Scholar for
    German pages while reporting ``Accept-Language: en-US`` describes a browser nobody has.
    Plain ``en`` is sent as ``en-US`` because that is the form a real browser sends.

    :param language: the ``--lang`` value, which becomes Scholar's ``hl``.
    :returns: the locale for the browser context.
    """
    return "en-US" if language == "en" else language


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
