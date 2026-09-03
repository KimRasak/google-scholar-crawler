"""Turning a failure into a diagnosis: what happened, and what to do next.

A crawl fails in ways the operator can act on — a refused connection, a proxy that rejects
TLS, a page that loaded but carries none of Scholar's markers — but the underlying libraries
report them as ``net::ERR_CONNECTION_REFUSED`` inside a Playwright call log, or as nothing at
all when a broken page merely looks like a search with no hits.

Every failure here names the likely cause in one line and lists concrete next steps, so the
message is worth reading without knowing the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Failure(str, Enum):
    """The class of failure a diagnosis describes."""

    CONNECTION_REFUSED = "connection_refused"
    DNS = "dns"
    OFFLINE = "offline"
    PROXY = "proxy"
    RESET = "reset"
    CERTIFICATE = "certificate"
    TIMEOUT = "timeout"
    BROWSER_CLOSED = "browser_closed"
    BROWSER_MISSING = "browser_missing"
    PATH_UNWRITABLE = "path_unwritable"
    HTTP_ERROR = "http_error"
    RATE_LIMITED = "rate_limited"
    UNKNOWN_LAYOUT = "unknown_layout"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class Diagnosis:
    """One failure, explained.

    :param failure: the class of failure.
    :param what: one line naming what happened and the likely cause.
    :param next_steps: concrete actions, most useful first.
    :param detail: the underlying error text, kept for when the diagnosis is wrong.
    """

    failure: Failure
    what: str
    next_steps: tuple[str, ...]
    detail: str = ""

    @property
    def retry_worthwhile(self) -> bool:
        """Whether repeating the same navigation could plausibly succeed.

        A refused connection, an unresolvable name or a rejected certificate will answer the
        same way every time, so retrying only delays the message the operator needs.

        :returns: True when the failure may be transient.
        """
        return self.failure in _TRANSIENT

    def render(self) -> list[str]:
        """Format the diagnosis for the terminal.

        :returns: printable lines, the first naming what happened.
        """
        lines = [self.what]
        lines.extend(f"try: {step}" for step in self.next_steps)
        if self.detail:
            lines.append(f"underlying error: {self.detail}")
        return lines


_TRANSIENT: frozenset[Failure] = frozenset(
    {Failure.TIMEOUT, Failure.RESET, Failure.OFFLINE, Failure.UNKNOWN}
)
"""Failures that a second attempt might get past."""


class CrawlFailure(RuntimeError):
    """A crawl that cannot continue, carrying its diagnosis.

    Subclasses ``RuntimeError`` because that is what a run already stops for; the diagnosis
    is what the operator reads instead of a library traceback.

    :param diagnosis: what happened and what to do next.
    """

    def __init__(self, diagnosis: Diagnosis) -> None:
        """Carry ``diagnosis`` as the reason this crawl stopped.

        :param diagnosis: what happened and what to do next.
        """
        super().__init__(diagnosis.what)
        self.diagnosis = diagnosis


_NETWORK_SIGNALS: tuple[tuple[str, Failure], ...] = (
    ("ERR_CONNECTION_REFUSED", Failure.CONNECTION_REFUSED),
    # Chromium keeps a fixed list of ports it will not open, so a --host on one of them fails
    # before any connection is attempted.
    ("ERR_UNSAFE_PORT", Failure.CONNECTION_REFUSED),
    ("ERR_NAME_NOT_RESOLVED", Failure.DNS),
    ("ERR_NAME_RESOLUTION_FAILED", Failure.DNS),
    ("ERR_INTERNET_DISCONNECTED", Failure.OFFLINE),
    ("ERR_NETWORK_CHANGED", Failure.OFFLINE),
    ("ERR_PROXY_CONNECTION_FAILED", Failure.PROXY),
    ("ERR_TUNNEL_CONNECTION_FAILED", Failure.PROXY),
    ("ERR_CONNECTION_RESET", Failure.RESET),
    ("ERR_CONNECTION_CLOSED", Failure.RESET),
    ("ERR_EMPTY_RESPONSE", Failure.RESET),
    ("ERR_CONNECTION_TIMED_OUT", Failure.TIMEOUT),
    ("ERR_CERT_", Failure.CERTIFICATE),
    ("SSL_", Failure.CERTIFICATE),
    ("Timeout", Failure.TIMEOUT),
    ("has been closed", Failure.BROWSER_CLOSED),
    ("Target closed", Failure.BROWSER_CLOSED),
)
"""Chromium and Playwright error fragments, most specific first."""

_NETWORK_ADVICE: dict[Failure, tuple[str, tuple[str, ...]]] = {
    Failure.CONNECTION_REFUSED: (
        "the host refused the connection, so nothing was crawled",
        (
            "open the same address in a normal browser: if that fails too, the network is blocking it",
            "check --host if you pointed it somewhere other than scholar.google.com",
            "a browser also refuses a --host port from its own blocked list, such as 9 or 6000",
            "check whether a VPN, firewall or corporate proxy is in the way",
        ),
    ),
    Failure.DNS: (
        "the host name could not be resolved, so nothing was crawled",
        (
            "check the spelling of --host",
            "check that this machine has working DNS (try opening any site in a browser)",
        ),
    ),
    Failure.OFFLINE: (
        "this machine has no working internet connection",
        ("reconnect and rerun; the run resumes from its cursor with --resume",),
    ),
    Failure.PROXY: (
        "the configured proxy refused or dropped the connection",
        (
            "check --proxy, including its credentials and port",
            "drop --proxy and rerun to confirm the proxy is the problem",
        ),
    ),
    Failure.RESET: (
        "the connection was closed mid-request, which is how networks usually drop automated traffic",
        (
            "raise --min-delay and --max-delay and rerun with --resume",
            "open Scholar by hand in the same profile once, then rerun",
            "check out/challenges.jsonl for takeovers just before this",
        ),
    ),
    Failure.CERTIFICATE: (
        "the TLS certificate was rejected, which usually means something is intercepting HTTPS",
        (
            "check whether a corporate proxy or antivirus is inspecting traffic",
            "check this machine's clock: a wrong date invalidates every certificate",
        ),
    ),
    Failure.TIMEOUT: (
        "the page did not finish loading in time",
        (
            "raise --nav-timeout if the connection is slow",
            "check whether the same address loads in a normal browser",
            "rerun with --resume; nothing already collected is lost",
        ),
    ),
    Failure.BROWSER_CLOSED: (
        "the browser window closed before the page loaded",
        (
            "leave the window alone while the crawl runs; close it only after the run reports",
            "rerun with --resume to continue from the cursor",
        ),
    ),
}
"""What each network failure means, and what to do about it."""


def diagnose_navigation(error: Exception, url: str) -> Diagnosis:
    """Explain a navigation that never completed.

    :param error: the Playwright error that ended the attempt.
    :param url: the URL being loaded.
    :returns: the diagnosis for this failure.
    """
    text = str(error)
    for needle, failure in _NETWORK_SIGNALS:
        if needle not in text:
            continue
        what, steps = _NETWORK_ADVICE[failure]
        return Diagnosis(
            failure=failure,
            what=f"{what} ({url})",
            next_steps=steps,
            detail=text.split("\n")[0],
        )
    return Diagnosis(
        failure=Failure.UNKNOWN,
        what=f"the browser could not load {url} and the reason is not one this tool recognizes",
        next_steps=(
            "rerun with --dump-html out/dump to keep the pages for inspection",
            "run --self-check to see whether plain Scholar still works from here",
        ),
        detail=text.split("\n")[0],
    )


_LAUNCH_SIGNALS: tuple[tuple[str, Failure], ...] = (
    ("Unsupported chromium channel", Failure.BROWSER_MISSING),
    ("is not found", Failure.BROWSER_MISSING),
    ("Executable doesn't exist", Failure.BROWSER_MISSING),
    ("Permission denied", Failure.PATH_UNWRITABLE),
    ("Read-only file system", Failure.PATH_UNWRITABLE),
    ("No space left", Failure.PATH_UNWRITABLE),
)
"""Fragments of a launch that never produced a window, most specific first."""


def stop_report(diagnosis: Diagnosis) -> str:
    """Render a diagnosis as the block a stopped run prints.

    :param diagnosis: what stopped the run.
    :returns: every line prefixed with ``[stop]``, opening with a blank line so the block is
        not read as part of the progress above it.
    """
    what, *rest = diagnosis.render()
    return "\n".join([f"\n[stop] {what}", *(f"[stop] {line}" for line in rest)])


def diagnose_unwritable(reason: str, flag: str) -> Diagnosis:
    """Explain an output path this run cannot write, before it spends anything.

    :param reason: what :func:`~scholar_crawler.storage.unwritable` found.
    :param flag: the flag carrying that path, which is what the reader has to change.
    :returns: the diagnosis for this failure.
    """
    return Diagnosis(
        failure=Failure.PATH_UNWRITABLE,
        what=f"{reason}, so nothing was crawled",
        next_steps=(
            f"point {flag} at a file this user can write",
            "run scholar-crawler --doctor, which checks every path a run needs",
        ),
    )


def diagnose_launch(error: Exception, *, channel: str | None, profile: Path) -> Diagnosis:
    """Explain a browser that never opened.

    Every request this tool makes goes through a real window, so a launch that fails stops
    everything before the first page. The causes are local — no browser of that name, or a
    profile directory it cannot write — and each has a different way out.

    :param error: the error the launch raised.
    :param channel: the browser channel asked for, None for Playwright's own Chromium.
    :param profile: the persistent-profile directory the launch was given.
    :returns: the diagnosis for this failure.
    """
    text = str(error)
    named = f"--channel {channel}" if channel else "the bundled Chromium"
    advice: dict[Failure, tuple[str, tuple[str, ...]]] = {
        Failure.BROWSER_MISSING: (
            f"no browser could be launched for {named}, so nothing was crawled",
            (
                "run scholar-crawler --doctor to see which browsers this machine has",
                "install Chrome, or pass --channel '' to use the Chromium Playwright downloads",
                "run scholar-crawler --install-browser if --doctor asks for it",
            ),
        ),
        Failure.PATH_UNWRITABLE: (
            f"the profile directory {profile} cannot be written, so no browser could start",
            (
                f"check the permissions on {profile} and its parents",
                "pass --profile somewhere writable, such as .scholar-profile in a project you own",
                "check free disk space if the path itself looks fine",
            ),
        ),
    }
    for needle, failure in _LAUNCH_SIGNALS:
        if needle in text:
            what, steps = advice[failure]
            return Diagnosis(failure=failure, what=what, next_steps=steps, detail=text.split("\n")[0])
    return Diagnosis(
        failure=Failure.UNKNOWN,
        what=f"the browser for {named} did not start, and the reason is not one this tool recognizes",
        next_steps=(
            "run scholar-crawler --doctor, which checks the same browser this run would launch",
            f"try a different --profile than {profile}, in case that directory is the problem",
        ),
        detail=text.split("\n")[0],
    )


def diagnose_page(url: str, *, status: int | None, title: str, dump: Path | None) -> Diagnosis:
    """Explain a page that loaded but carries no Scholar content.

    :param url: the URL that was loaded.
    :param status: HTTP status of the navigation, when known.
    :param title: the page title, used to describe an error page.
    :param dump: where the page was saved, when dumping is enabled.
    :returns: the diagnosis for this page.
    """
    saved = f"the page was saved to {dump}" if dump else "rerun with --dump-html out/dump to keep the page"
    if status is not None and status in (429, 503):
        return Diagnosis(
            failure=Failure.RATE_LIMITED,
            what=f"Scholar answered HTTP {status}: it is refusing requests from here for now ({url})",
            next_steps=(
                "stop for a while — an hour is not excessive — before rerunning with --resume",
                "raise --min-delay and --max-delay, and lower --pages per run",
                "open Scholar by hand in the same profile to see what it asks for",
            ),
        )
    if status is not None and status >= 400:
        return Diagnosis(
            failure=Failure.HTTP_ERROR,
            what=f"Scholar answered HTTP {status} for {url}",
            next_steps=(
                "open the same URL in a normal browser to see whether it is broken for everyone",
                "rerun with --resume once it works again",
                saved,
            ),
            detail=f"page title: {title}" if title else "",
        )
    return Diagnosis(
        failure=Failure.UNKNOWN_LAYOUT,
        what=f"the page loaded but carries none of Scholar's markers, so nothing could be parsed ({url})",
        next_steps=(
            "run --self-check: it reports field by field what the parser can still read",
            saved,
            "check whether a captive portal or a consent page is being served instead",
            "if Scholar changed its layout, the selectors in parser.py need updating",
        ),
        detail=f"page title: {title}" if title else "",
    )


def diagnose_challenge_loop(url: str, attempts: int) -> Diagnosis:
    """Explain a page that keeps answering with a challenge after every takeover.

    :param url: the URL that never came back with content.
    :param attempts: how many times it was tried.
    :returns: the diagnosis for this failure.
    """
    return Diagnosis(
        failure=Failure.RATE_LIMITED,
        what=(
            f"the same page answered with a verification challenge {attempts} times in a row, "
            f"so this address is being blocked rather than merely checked ({url})"
        ),
        next_steps=(
            "stop for a while before rerunning with --resume; the profile keeps the cleared cookies",
            "raise --min-delay and --max-delay, and lower --pages per run",
            "check out/challenges.jsonl: back-to-back takeovers mean the rhythm is still too fast",
        ),
    )
