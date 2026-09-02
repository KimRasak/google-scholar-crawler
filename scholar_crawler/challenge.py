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
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

RESULTS_SELECTOR = "div.gs_r.gs_or.gs_scl, div#gs_res_ccl_mid, div.gs_med, #gsc_a_b, #gsc_prf_in"
"""Selectors proving a page carries Scholar content: result cards, the zero-hit
notice, an author's publication table, or an author profile header."""


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


@dataclass(slots=True, frozen=True)
class Takeover:
    """What happened while the human held the window.

    :param waited: seconds the crawler waited.
    :param saw: challenge kinds observed, in order, without repeats — a wait that began as a
        captcha and ended at a sign-in wall required two different actions from the human.
    """

    waited: float
    saw: tuple[str, ...]

    def describe(self) -> str:
        """Summarize the wait in one line.

        :returns: how long the human took, and what the page showed while they worked.
        """
        return f"cleared after {self.waited:.0f}s (saw {' -> '.join(self.saw)})"


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
    :param status_every: seconds between progress lines while waiting.
    :param warn_before: ring and warn again this long before the timeout elapses.
    """

    timeout: float = 600.0
    poll_interval: float = 2.0
    headless: bool = False
    status_every: float = 15.0
    warn_before: float = 60.0

    def resolve(self, page: Page, challenge: Challenge) -> Takeover:
        """Wait until the human clears ``challenge`` on ``page``.

        :param page: the page showing the challenge; brought to the front for the human.
        :param challenge: the detected challenge being handed over.
        :returns: how long the human took and what the window showed while they worked.
        :raises ChallengeUnattended: when running headless, or when ``timeout`` elapses first.
        """
        if self.headless:
            raise ChallengeUnattended(
                f"{challenge.kind.value} at {challenge.url} ({challenge.detail}); "
                "rerun without --headless so the challenge can be solved by hand — "
                "the persistent profile keeps the cleared cookies for later runs"
            )
        _bell()
        budget = (
            f"{self.timeout:.0f}s to act" if self.timeout else "no time limit; it waits as long as needed"
        )
        print(
            f"\n[handoff] {challenge.kind.value}: {challenge.detail}\n"
            f"[handoff] URL: {challenge.url}\n"
            "[handoff] The browser window is yours. Solve the challenge (or accept the\n"
            "[handoff] consent/sign-in page) and leave it on the Scholar result page.\n"
            f"[handoff] No keypress needed — the page is re-checked every {self.poll_interval:g}s "
            f"and crawling resumes by itself. You have {budget}.\n"
            "[handoff] Press Ctrl+C to stop instead.",
            flush=True,
        )
        with suppress(PlaywrightError):  # window already gone; the wait below reports it
            page.bring_to_front()
        return self._wait_out(page, challenge)

    def _wait_out(self, page: Page, challenge: Challenge) -> Takeover:
        """Poll until the challenge is gone, keeping the human informed while waiting.

        A silent terminal is the worst thing to leave in front of someone who stepped away, so
        the wait reports how long is left, says when the page turns into a different challenge,
        and rings again before it gives up.

        :param page: the page showing the challenge.
        :param challenge: the challenge that was handed over.
        :returns: what the wait observed.
        :raises ChallengeUnattended: when the page is closed or the timeout elapses.
        """
        started = time.monotonic()
        deadline = started + self.timeout if self.timeout else None
        seen = [challenge.kind.value]
        spoken = started
        warned = False
        while True:
            time.sleep(self.poll_interval)
            if page.is_closed():
                raise ChallengeUnattended("browser page was closed during handoff")
            now = time.monotonic()
            waited = now - started
            current = detect_challenge(page)
            if current is None:
                print(f"[handoff] cleared after {waited:.0f}s — resuming automated crawl.", flush=True)
                return Takeover(waited=waited, saw=tuple(seen))
            if current.kind.value != seen[-1]:
                seen.append(current.kind.value)
                print(
                    f"[handoff] the page is now a {current.kind.value}: {current.detail}",
                    flush=True,
                )
                spoken = now
            left = None if deadline is None else deadline - now
            if left is not None and not warned and left <= self.warn_before:
                warned = True
                spoken = now
                _bell()
                print(
                    f"[handoff] still waiting; {left:.0f}s left before the run gives up "
                    "and stops with whatever it collected",
                    flush=True,
                )
            elif now - spoken >= self.status_every:
                spoken = now
                remaining = "" if left is None else f", {left:.0f}s left"
                print(
                    f"[handoff] waiting {waited:.0f}s so far{remaining}; "
                    f"still showing {current.kind.value}",
                    flush=True,
                )
            if left is not None and left <= 0:
                raise ChallengeUnattended(
                    f"challenge still present after {self.timeout:.0f}s of waiting for a human; "
                    f"the window showed {' -> '.join(seen)}"
                )
