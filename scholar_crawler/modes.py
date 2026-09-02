"""The modes that run instead of a crawl.

``--doctor``, ``--self-check``, ``--rehearse-handoff``, ``--show-state`` and ``--forget`` each
answer a question about the tool rather than collecting records: can this machine run a crawl
at all, does Scholar still parse, does the takeover path work, where did previous runs stop,
and drop that progress. They share the crawler's browser settings and its takeover log but
none of its paging, so they live apart from both the crawl loop and argument parsing.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

from .browser import Session, browser_session
from .challenge import ChallengeUnattended
from .crawler import ScholarCrawler
from .doctor import Status, diagnose_environment, render_environment
from .models import SearchRequest
from .rehearsal import rehearse
from .selfcheck import check_page, report
from .storage import ChallengeLog, StateStore

SELF_CHECK_QUERY = "machine learning"
"""Broad query used by :func:`self_check`: many hits, PDFs, citations and a next page."""

RECENT_TAKEOVERS = 5
"""How many of the most recent takeovers :func:`show_state` prints."""


def check_environment(*, profile: Path, out: Path, state: Path, channel: str | None) -> int:
    """Report whether this machine can run a crawl, without sending a request.

    :param profile: profile directory a crawl would reuse.
    :param out: JSONL destination a crawl would write.
    :param state: resume-state file a crawl would write.
    :param channel: browser channel a crawl would drive.
    :returns: process exit code — 0 when nothing is broken, 1 when something must be fixed.
    """
    findings = diagnose_environment(profile=profile, out=out, state=state, channel=channel)
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
    else:
        print(f"[state] nothing stored in {state_path}", flush=True)
    show_takeovers(challenge_log)
    return 0


def show_takeovers(path: Path, limit: int = RECENT_TAKEOVERS) -> None:
    """Print the most recent human takeovers recorded in the challenge log.

    :param path: challenge-log path; a missing file prints nothing.
    :param limit: how many of the most recent takeovers to print.
    """
    takeovers = ChallengeLog(path).entries()
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
    :param pattern: case-insensitive signature substring; an empty pattern drops everything.
    :returns: process exit code — always 0, including when nothing matched.
    """
    state = StateStore(state_path)
    state.load()
    removed = state.forget(pattern)
    if not removed:
        print(f"[state] no stored target matches {pattern!r}", flush=True)
        return 0
    print(f"[state] dropped {len(removed)} target(s) from {state_path}", flush=True)
    for entry in removed:
        print(f"[state]   {entry.describe()}", flush=True)
    print("[state] those targets will be crawled from the start again", flush=True)
    return 0
