"""The modes that run instead of a crawl.

``--install-browser``, ``--doctor``, ``--self-check``, ``--rehearse-handoff``,
``--show-state`` and ``--forget`` each answer a question about the tool rather than collecting
records: finish the installation, can this machine run a crawl at all, does Scholar still
parse, does the takeover path work, where did previous runs stop, and drop that progress. They
share the crawler's browser settings and its takeover log but none of its paging, so they live
apart from both the crawl loop and argument parsing.
"""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from pathlib import Path

from .browser import Session, browser_session
from .challenge import ChallengeUnattended
from .crawler import ScholarCrawler
from .diagnose import CrawlFailure, stop_report
from .doctor import Status, check_browser, diagnose_environment, render_environment
from .models import SearchRequest, parse_signature
from .recipes import resume_command
from .rehearsal import rehearse
from .selfcheck import check_page, report
from .storage import ChallengeLog, StateStore
from .text import counted

SELF_CHECK_QUERY = "machine learning"
"""Broad query used by :func:`self_check`: many hits, PDFs, citations and a next page."""

RECENT_TAKEOVERS = 5
"""How many of the most recent takeovers :func:`show_state` prints."""


def install_browser() -> int:
    """Download the browser Playwright drives into this installation.

    A fresh install has the library but no browser, and the download is the one step a user
    cannot guess. Running it through this interpreter is what makes it land in the right
    place when the tool was installed into its own environment, as pipx does.

    :returns: process exit code — 0 when a browser is available afterwards, 1 when not.
    """
    # Measured on a fresh install: 274 MB comes down (Chromium, its headless shell, ffmpeg) and
    # 550 MB stays on disk. Understating it invites a cancelled download halfway through.
    print(
        "[install] downloading Chromium for Playwright: about 280 MB, 550 MB on disk, once",
        flush=True,
    )
    completed = subprocess.run(  # noqa: S603 - fixed command, no user input
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )
    if completed.returncode != 0:
        print(
            f"[install] playwright install exited {completed.returncode}; "
            f"run '{sys.executable} -m playwright install chromium' to see why",
            file=sys.stderr,
        )
        return 1
    finding = check_browser(None)
    print(f"[install] {finding.describe()}", flush=True)
    if finding.status is Status.FAIL:
        return 1
    print("[install] ready; run --doctor to check the rest, then --self-check", flush=True)
    return 0


def check_environment(
    *, profile: Path, written: list[tuple[str, Path, str]], channel: str | None
) -> int:
    """Report whether this machine can run a crawl, without sending a request.

    :param profile: profile directory a crawl would reuse.
    :param written: every path a crawl would write, as flag, path and ``file`` or ``dir``.
    :param channel: browser channel a crawl would drive.
    :returns: process exit code — 0 when nothing is broken, 1 when something must be fixed.
    """
    findings = diagnose_environment(profile=profile, written=written, channel=channel)
    for line in render_environment(findings):
        print(f"[doctor] {line}", flush=True)
    return 1 if any(finding.status is Status.FAIL for finding in findings) else 0


def self_check(session: Session) -> int:
    """Fetch one page and report whether the parser still finds every field.

    :param session: browser settings for the single request.
    :returns: process exit code — 0 when every check passed, 1 otherwise, 130 on Ctrl+C.
    """
    print(f"[check] fetching one page for {SELF_CHECK_QUERY!r}", flush=True)
    try:
        with browser_session(session.options) as (_context, page):
            crawler = ScholarCrawler(
                page,
                session.handoff,
                host=session.host,
                max_handoffs=session.max_handoffs,
                dump_dir=session.dump_dir,
                challenge_log=session.log,
            )
            request = SearchRequest(query=SELF_CHECK_QUERY, language=session.language)
            fetched = crawler.fetch_page(request, 0)
    except KeyboardInterrupt:
        print("\n[stop] interrupted by user", flush=True)
        return 130
    except CrawlFailure as failure:
        print(stop_report(failure.diagnosis), file=sys.stderr)
        return 1
    except (ChallengeUnattended, RuntimeError) as error:
        print(f"\n[stop] {error}", file=sys.stderr)
        return 1
    return 0 if report(check_page(fetched)) else 1


def rehearse_takeover(session: Session) -> int:
    """Exercise the human-takeover path against a local page, without requesting anything.

    :param session: browser settings and the takeover policy under test.
    :returns: process exit code — 0 when the takeover path worked (or refused as designed
        under ``--headless``), 1 when it did not, 130 on Ctrl+C.
    """
    print(
        "[rehearse] opening a local challenge page in the crawling profile; "
        "no request is sent to Google",
        flush=True,
    )
    try:
        with browser_session(session.options) as (_context, page):
            return 0 if rehearse(page, session.handoff, session.log) else 1
    except KeyboardInterrupt:
        print("\n[stop] interrupted by user", flush=True)
        return 130
    except CrawlFailure as failure:
        # A drill opens the crawling profile, so it meets the same launch failures a crawl does.
        print(stop_report(failure.diagnosis), file=sys.stderr)
        return 1
    except ChallengeUnattended as error:
        if session.handoff.headless:
            print(f"[rehearse] refused without a window, as designed: {error}", flush=True)
            return 0
        print(f"\n[stop] {error}", file=sys.stderr)
        return 1


def show_state(state_path: Path, challenge_log: Path) -> int:
    """Print the resume progress and the takeovers previous runs recorded.

    :param state_path: resume-state path.
    :param challenge_log: takeover-log path; a missing file prints nothing.
    :returns: process exit code — always 0, including for an empty state file.
    """
    state = StateStore(state_path)
    state.load()
    entries = state.entries()
    if entries:
        done = sum(1 for entry in entries if entry.exhausted)
        print(f"[state] {len(entries)} targets in {state_path} ({done} finished)", flush=True)
        for entry in entries:
            print(f"[state]   {entry.describe()}", flush=True)
            request = parse_signature(entry.signature)
            if request is not None and not entry.exhausted:
                # A cursor is only useful to whoever can reproduce its target; days later that
                # means retyping the filters, so the command comes back ready to paste.
                print(f"[state]     $ {resume_command(request, state_path)}", flush=True)
    else:
        print(f"[state] nothing stored in {state_path}", flush=True)
    if state.repaired:
        print(
            f"[state] {counted(state.repaired, 'stored cursor')} had a field of the wrong type, "
            "read as absent: those targets start over",
            flush=True,
        )
    show_takeovers(challenge_log)
    return 0


def show_takeovers(path: Path, limit: int = RECENT_TAKEOVERS) -> None:
    """Print the most recent human takeovers recorded in the challenge log.

    :param path: challenge-log path; a missing file prints nothing.
    :param limit: how many of the most recent takeovers to print.
    """
    takeovers, unreadable = ChallengeLog(path).read()
    if unreadable:
        print(f"[handoff] {counted(unreadable, 'line')} in {path} could not be read", flush=True)
    if not takeovers:
        return
    kinds = Counter(entry.kind for entry in takeovers)
    breakdown = ", ".join(f"{kind} x{count}" for kind, count in sorted(kinds.items()))
    print(f"[handoff] {len(takeovers)} takeovers in {path} ({breakdown})", flush=True)
    for entry in takeovers[-limit:]:
        print(f"[handoff]   {entry.describe()}", flush=True)
        print(f"[handoff]     {entry.reason} at {entry.url}", flush=True)


def forget_state(state_path: Path, pattern: str) -> int:
    """Drop stored progress for the targets matching ``pattern``.

    :param state_path: resume-state path.
    :param pattern: case-insensitive substring of either spelling of a target; an empty pattern
        drops everything.
    :returns: process exit code — always 0, including when nothing matched.
    """
    state = StateStore(state_path)
    state.load()
    kept = state.entries()
    removed = state.forget(pattern)
    if not removed:
        print(f"[state] no stored target matches {pattern!r}", flush=True)
        # A miss is almost always a pattern typed from memory, so the alternatives are the answer.
        for entry in kept:
            print(f"[state]   stored: {entry.target}", flush=True)
        if not kept:
            print(f"[state] {state_path} stores no target at all", flush=True)
        return 0
    print(f"[state] dropped {counted(len(removed), 'target')} from {state_path}", flush=True)
    for entry in removed:
        print(f"[state]   {entry.describe()}", flush=True)
    print("[state] those targets will be crawled from the start again", flush=True)
    return 0
