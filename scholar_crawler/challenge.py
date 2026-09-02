"""Challenge detection and human takeover.

The crawler drives ordinary searches on its own. Anything that asks a human to
prove intent — reCAPTCHA, the ``/sorry/`` interstitial, a consent wall, a sign-in
prompt — stops automation, hands the visible browser window to the operator and
resumes only once the page is a normal result page again. Nothing here attempts
to solve, bypass or suppress a challenge.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

RESULTS_SELECTOR = "div.gs_r.gs_or.gs_scl, div#gs_res_ccl_mid, div.gs_med"


class ChallengeKind(str, Enum):
    """The class of human-verification page currently displayed."""

    CAPTCHA = "captcha"
    """A reCAPTCHA or image challenge, on Scholar or on the /sorry/ interstitial."""

    RATE_LIMIT = "rate_limit"
    """A block page with no solvable challenge; Google is refusing the traffic."""

    CONSENT = "consent"
    """A cookie/terms consent wall that must be accepted before searching."""

    SIGN_IN = "sign_in"
    """A Google account sign-in wall."""


class ChallengeUnattended(RuntimeError):
    """Raised when a challenge appears but no human can act on it."""


_TEXT_SIGNALS: tuple[tuple[str, ChallengeKind], ...] = (
    ("unusual traffic", ChallengeKind.RATE_LIMIT),
    ("异常流量", ChallengeKind.RATE_LIMIT),
    ("automated queries", ChallengeKind.RATE_LIMIT),
    ("i'm not a robot", ChallengeKind.CAPTCHA),
    ("我不是机器人", ChallengeKind.CAPTCHA),
    ("type the characters", ChallengeKind.CAPTCHA),
    ("before you continue", ChallengeKind.CONSENT),
    ("继续前往", ChallengeKind.CONSENT),
)

_SELECTOR_SIGNALS: tuple[tuple[str, ChallengeKind], ...] = (
    ("#gs_captcha_ccl", ChallengeKind.CAPTCHA),
    ("#gs_captcha_f", ChallengeKind.CAPTCHA),
    ("form#captcha-form", ChallengeKind.CAPTCHA),
    ("iframe[src*='recaptcha']", ChallengeKind.CAPTCHA),
    ("div.g-recaptcha", ChallengeKind.CAPTCHA),
)


@dataclass(slots=True)
class Challenge:
    """A detected human-verification page."""

    kind: ChallengeKind
    url: str
    detail: str


def detect_challenge(page: Page) -> Challenge | None:
    """Classify the page currently loaded in ``page``.

    :param page: the Scholar page to inspect.
    :returns: the detected challenge, or None when the page looks like ordinary content.
    """
    try:
        url = page.url
        if "/sorry/" in url or "sorry/index" in url:
            return Challenge(ChallengeKind.CAPTCHA, url, "Google /sorry/ interstitial")
        if url.startswith("https://consent.google."):
            return Challenge(ChallengeKind.CONSENT, url, "consent wall")
        if "accounts.google.com" in url:
            return Challenge(ChallengeKind.SIGN_IN, url, "account sign-in wall")
        for selector, kind in _SELECTOR_SIGNALS:
            if page.locator(selector).count() > 0:
                return Challenge(kind, url, f"matched {selector}")
        body = (page.inner_text("body", timeout=5_000) or "").lower()
        has_results = page.locator(RESULTS_SELECTOR).count() > 0
    except PlaywrightError:  # page navigating or closed mid-inspection; caller retries
        return None
    if has_results:
        return None
    for needle, kind in _TEXT_SIGNALS:
        if needle in body:
            return Challenge(kind, url, f"page text contains {needle!r}")
    return None


def _bell() -> None:
    """Draw the operator's attention with a terminal bell and, on macOS, a sound."""
    sys.stderr.write("\a")
    sys.stderr.flush()
    if sys.platform == "darwin" and (afplay := shutil.which("afplay")):
        sound = "/System/Library/Sounds/Ping.aiff"
        subprocess.Popen(  # noqa: S603 - fixed binary and asset path
            [afplay, sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


@dataclass(slots=True)
class HumanHandoff:
    """Blocks the crawler while a human resolves a challenge in the browser window.

    :param timeout: seconds to wait for the human; 0 waits indefinitely.
    :param poll_interval: seconds between page re-inspections.
    :param headless: when True there is no window to hand over, so challenges abort the run.
    """

    timeout: float = 600.0
    poll_interval: float = 2.0
    headless: bool = False

    def resolve(self, page: Page, challenge: Challenge) -> None:
        """Wait until the human clears ``challenge`` on ``page``.

        :param page: the page showing the challenge; brought to the front for the human.
        :param challenge: the detected challenge being handed over.
        :raises ChallengeUnattended: when running headless, or when ``timeout`` elapses first.
        """
        if self.headless:
            raise ChallengeUnattended(
                f"{challenge.kind.value} at {challenge.url} ({challenge.detail}); "
                "rerun without --headless so the challenge can be solved by hand — "
                "the persistent profile keeps the cleared cookies for later runs"
            )
        _bell()
        print(
            f"\n[handoff] {challenge.kind.value}: {challenge.detail}\n"
            f"[handoff] URL: {challenge.url}\n"
            "[handoff] The browser window is yours. Solve the challenge (or accept the\n"
            "[handoff] consent/sign-in page) and leave it on the Scholar result page.\n"
            "[handoff] Crawling resumes automatically; press Ctrl+C to stop.",
            flush=True,
        )
        try:
            page.bring_to_front()
        except PlaywrightError:  # window already gone; the wait below reports it
            pass
        deadline = time.monotonic() + self.timeout if self.timeout else None
        while True:
            time.sleep(self.poll_interval)
            if page.is_closed():
                raise ChallengeUnattended("browser page was closed during handoff")
            if detect_challenge(page) is None:
                print("[handoff] cleared — resuming automated crawl.", flush=True)
                return
            if deadline is not None and time.monotonic() > deadline:
                raise ChallengeUnattended(
                    f"challenge still present after {self.timeout:.0f}s of waiting for a human"
                )
