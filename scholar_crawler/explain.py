"""Reading a command back in plain words, before it runs.

There are more than forty flags, and a wrong combination does not fail — it quietly does
something other than what was meant: ``--headless`` gives up at the first challenge,
``--resume`` without a stored cursor starts over, an omitted ``--resume`` recollects what is
already on disk, ``--follow-breadth 0`` turns expansion off while looking enabled. ``--dry-run``
states what this exact command will do, which files it will touch, and which of its flags
contradict or cancel each other.

The cost estimate is not repeated here; ``--dry-run`` owns that and can be combined with this.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .browser import BrowserOptions
from .config import Sources
from .crawler import DEFAULT_MAX_DELAY, DEFAULT_MIN_DELAY, Pacing
from .expand import FollowPolicy
from .models import AuthorRequest, SearchRequest
from .storage import ChallengeLog, StateStore, profiles_beside
from .urls import SCHOLAR_HOST


class Level(str, Enum):
    """How much a concern matters."""

    NOTE = "note"
    WARN = "warn"


@dataclass(slots=True, frozen=True)
class Concern:
    """One flag combination worth knowing about before the run starts.

    :param level: ``note`` for a consequence worth stating, ``warn`` for a flag that does not
        do what it looks like it does.
    :param message: what the combination means, in one line.
    """

    level: Level
    message: str

    def describe(self) -> str:
        """Format the concern for the terminal.

        :returns: level and message.
        """
        return f"{self.level.value}: {self.message}"


def _targets(listings: list[SearchRequest], authors: list[AuthorRequest]) -> list[str]:
    """Describe what will be crawled.

    :param listings: keyword and citation listings.
    :param authors: author profiles.
    :returns: one line naming the counts, then one line per target.
    """
    parts = []
    if listings:
        parts.append(f"{len(listings)} listing(s)")
    if authors:
        parts.append(f"{len(authors)} author profile(s)")
    lines = [f"crawling {' and '.join(parts)}"]
    lines.extend(f"  target: {request.label}" for request in listings)
    lines.extend(f"  target: author {request.user_id}" for request in authors)
    return lines


def _paging(args: argparse.Namespace, listings: list[SearchRequest]) -> list[str]:
    """Describe how far each target will be paged.

    :param args: parsed arguments.
    :param listings: keyword and citation listings, which are paged 10 records at a time.
    :returns: the lines describing depth per target.
    """
    lines = []
    if listings:
        cap = f", stopping at {args.max_results} records each" if args.max_results else ""
        lines.append(f"up to {args.pages} page(s) per listing, 10 records a page{cap}")
    if args.start:
        lines.append(f"starting at result offset {args.start}")
    return lines


def _filters(args: argparse.Namespace) -> list[str]:
    """Describe the filters applied to every target.

    :param args: parsed arguments.
    :returns: one line listing the active filters, or nothing when all are default.
    """
    active = []
    if args.year_from or args.year_to:
        active.append(f"years {args.year_from or 'any'}–{args.year_to or 'any'}")
    if args.sort_by_date:
        active.append("sorted by date")
    if args.no_citations:
        active.append("citation-only records excluded")
    if args.no_patents:
        active.append("patents excluded")
    if args.review_only:
        active.append("review articles only")
    if args.lang != "en":
        active.append(f"interface language {args.lang}")
    return [f"filters: {', '.join(active)}"] if active else []


def _expansion(follow: FollowPolicy, seeds: int) -> list[str]:
    """Describe the citation-graph expansion.

    :param follow: expansion policy.
    :param seeds: number of seed targets.
    :returns: the lines describing the expansion, or nothing when it is off.
    """
    if not follow.enabled:
        return []
    return [
        f"then following citations {follow.depth} level(s) deep, expanding the "
        f"{follow.breadth} most-cited records per level: up to {follow.estimate(seeds)} listings"
    ]


def _rhythm(pacing: Pacing) -> list[str]:
    """Describe the request rhythm.

    :param pacing: the resolved pacing.
    :returns: the lines describing delays, pauses and timeouts.
    """
    lines = [f"waiting {pacing.min_delay:g}–{pacing.max_delay:g}s between page loads"]
    if pacing.cooldown_every:
        lines.append(
            f"pausing {pacing.cooldown_seconds:g}s every {pacing.cooldown_every} loads, "
            f"and giving up on a page after {pacing.nav_timeout:g}s"
        )
    return lines


def _takeover(args: argparse.Namespace) -> list[str]:
    """Describe what a challenge will do.

    :param args: parsed arguments.
    :returns: the lines describing the takeover policy.
    """
    if args.headless:
        return ["on a challenge: nothing to hand over without a window, so the run stops"]
    waiting = "waiting forever" if not args.handoff_timeout else f"waiting up to {args.handoff_timeout:g}s"
    return [
        f"on a challenge: the window is brought to you, {waiting} for you to clear it, "
        f"up to {args.max_handoffs} time(s) this run",
        f"after each takeover the delays widen by x{args.backoff_factor:g}",
    ]


def _browser(options: BrowserOptions, given: str | None) -> list[str]:
    """Describe what the window will claim to be.

    The launch settings are read back rather than recomputed, so this line is what the run is
    about to do and not a second opinion about it.

    :param options: the launch settings this run resolved.
    :param given: the timezone the command line asked for, when it named one.
    :returns: the line naming the locale and timezone the window reports.
    """
    source = "yours" if given else "matching the language"
    return [
        f"the window sends Accept-Language {options.locale} "
        f"and reports its clock in {options.timezone} ({source})"
    ]


def _files(args: argparse.Namespace) -> list[str]:
    """Describe every file the run will touch.

    :param args: parsed arguments.
    :returns: one line per file, saying whether it is created or appended to.
    """
    planned: list[tuple[str, Path | None]] = [
        ("records", args.out),
        ("bibtex", args.bibtex),
        ("author profiles", profiles_beside(args.out) if args.author else None),
        ("resume state", args.state),
        ("takeover log", args.challenge_log),
        ("page dumps", args.dump_html),
    ]
    lines = []
    for label, path in planned:
        if path is None:
            continue
        verb = "appending to" if path.exists() else "creating"
        lines.append(f"{verb} {label}: {path}")
    return lines


def _output_collisions(args: argparse.Namespace) -> list[Concern]:
    """Find two outputs pointed at one path.

    :param args: parsed arguments.
    :returns: one concern per collision.
    """
    named = {
        "--out": args.out,
        "--bibtex": args.bibtex,
        "--state": args.state,
        "--challenge-log": args.challenge_log,
        "the profile file beside --out": profiles_beside(args.out) if args.author else None,
    }
    seen: dict[Path, str] = {}
    concerns = []
    for flag, path in named.items():
        if path is None:
            continue
        if path in seen:
            concerns.append(
                Concern(Level.WARN, f"{flag} and {seen[path]} write to the same file: {path}")
            )
        seen[path] = flag
    return concerns


def _resume_concerns(
    args: argparse.Namespace, listings: list[SearchRequest], authors: list[AuthorRequest]
) -> list[Concern]:
    """Compare the request against the cursors previous runs stored.

    :param args: parsed arguments.
    :param listings: keyword and citation listings.
    :param authors: author profiles.
    :returns: concerns about resuming, or the absence of it. A stored cursor that this run
        ignores is reported by the run itself, so it is not repeated here.
    """
    state = StateStore(args.state)
    state.load()
    stored = [
        request
        for request in (*listings, *authors)
        if state.next_start(request.signature()) > 0
    ]
    if args.resume and not stored:
        return [
            Concern(
                Level.WARN,
                f"--resume was given but {args.state} holds no cursor for these targets, "
                "so every one starts from the beginning",
            )
        ]
    if args.resume and args.start:
        return [
            Concern(
                Level.WARN,
                f"--start {args.start} is ignored for the {len(stored)} target(s) that have a "
                "stored cursor; --resume wins",
            )
        ]
    return []


def _pacing_concerns(args: argparse.Namespace, pacing: Pacing) -> list[Concern]:
    """Judge the rhythm against the defaults and the takeover history.

    :param args: parsed arguments.
    :param pacing: the resolved pacing.
    :returns: concerns about the rhythm.
    """
    concerns = []
    if pacing.min_delay < DEFAULT_MIN_DELAY or pacing.max_delay < DEFAULT_MAX_DELAY:
        concerns.append(
            Concern(
                Level.WARN,
                f"{pacing.min_delay:g}–{pacing.max_delay:g}s is faster than the default "
                f"{DEFAULT_MIN_DELAY:g}–{DEFAULT_MAX_DELAY:g}s, which invites challenges",
            )
        )
    if not pacing.cooldown_every:
        concerns.append(
            Concern(Level.WARN, "--cooldown-every 0 removes the long pause; long runs get blocked")
        )
    if args.no_learn_from_history:
        takeovers = ChallengeLog(args.challenge_log).entries()
        if takeovers:
            concerns.append(
                Concern(
                    Level.WARN,
                    f"--no-learn-from-history ignores {len(takeovers)} recorded takeover(s) that "
                    "would otherwise start this run slower",
                )
            )
    if not args.challenge_cooldown:
        concerns.append(
            Concern(
                Level.NOTE,
                "--challenge-cooldown 0 resumes immediately after back-to-back challenges",
            )
        )
    return concerns


def _flag_concerns(
    args: argparse.Namespace,
    listings: list[SearchRequest],
    authors: list[AuthorRequest],
    follow: FollowPolicy,
) -> list[Concern]:
    """Find flags that cancel each other or do not do what they look like.

    :param args: parsed arguments.
    :param listings: keyword and citation listings.
    :param authors: author profiles.
    :param follow: expansion policy.
    :returns: concerns about the combination given.
    """
    concerns = []
    if args.headless:
        concerns.append(
            Concern(
                Level.WARN,
                "--headless cannot hand a challenge to anyone, so the first one ends the run "
                "with whatever was collected",
            )
        )
    if args.year_from and args.year_to and args.year_from > args.year_to:
        concerns.append(
            Concern(
                Level.WARN,
                f"--year-from {args.year_from} is later than --year-to {args.year_to}: "
                "Scholar will return nothing",
            )
        )
    if args.pages < 1 and listings:
        concerns.append(Concern(Level.WARN, f"--pages {args.pages} loads no listing page at all"))
    if args.max_results is not None and args.max_results < 10:
        concerns.append(
            Concern(
                Level.NOTE,
                f"--max-results {args.max_results} still costs a whole page load; Scholar has no "
                "smaller unit",
            )
        )
    if follow.enabled and args.no_citations:
        concerns.append(
            Concern(
                Level.NOTE,
                "expansion follows citation listings, which --no-citations then filters out of "
                "the results",
            )
        )
    if args.bibtex and authors:
        concerns.append(
            Concern(
                Level.NOTE,
                "profile publications need their card id resolved first, so BibTeX costs three "
                "page loads per record instead of two",
            )
        )
    if not args.max_handoffs:
        concerns.append(Concern(Level.WARN, "--max-handoffs 0 ends the run at the first challenge"))
    if args.dump_html:
        concerns.append(
            Concern(
                Level.NOTE,
                f"every fetched page is written to {args.dump_html}, including pages carrying "
                "session material",
            )
        )
    if args.proxy:
        concerns.append(
            Concern(
                Level.NOTE,
                "all traffic goes through the proxy; datacenter addresses are challenged far more",
            )
        )
    if args.host != SCHOLAR_HOST:
        concerns.append(Concern(Level.NOTE, f"requests go to {args.host}, not {SCHOLAR_HOST}"))
    return concerns


def concerns_of(
    args: argparse.Namespace,
    listings: list[SearchRequest],
    authors: list[AuthorRequest],
    follow: FollowPolicy,
    pacing: Pacing,
) -> list[Concern]:
    """Collect every concern about this command, warnings first.

    :param args: parsed arguments.
    :param listings: keyword and citation listings.
    :param authors: author profiles.
    :param follow: expansion policy.
    :param pacing: the resolved pacing.
    :returns: the concerns, ``warn`` before ``note``.
    """
    found = [
        *_flag_concerns(args, listings, authors, follow),
        *_resume_concerns(args, listings, authors),
        *_pacing_concerns(args, pacing),
        *_output_collisions(args),
    ]
    return [concern for concern in found if concern.level is Level.WARN] + [
        concern for concern in found if concern.level is Level.NOTE
    ]


def explain(
    args: argparse.Namespace,
    listings: list[SearchRequest],
    authors: list[AuthorRequest],
    follow: FollowPolicy,
    pacing: Pacing,
    options: BrowserOptions,
    sources: Sources | None = None,
) -> list[str]:
    """Describe this command in plain words, then list what is worth knowing about it.

    :param args: parsed arguments.
    :param listings: keyword and citation listings.
    :param authors: author profiles.
    :param follow: expansion policy.
    :param pacing: the resolved pacing.
    :param options: the launch settings this run resolved, described as they are.
    :param sources: where the settings in effect came from, when a settings file was read.
    :returns: printable lines.
    """
    lines = [
        *(sources.describe() if sources is not None else []),
        *_targets(listings, authors),
        *_paging(args, listings),
        *_filters(args),
        *_expansion(follow, len(listings) + len(authors)),
        *_rhythm(pacing),
        *_takeover(args),
        *_browser(options, args.timezone),
        *_files(args),
    ]
    found = concerns_of(args, listings, authors, follow, pacing)
    if found:
        lines.append("")
        lines.extend(concern.describe() for concern in found)
    return lines
