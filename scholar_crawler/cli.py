"""Command-line entry point for the Scholar crawler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .browser import BrowserOptions, Session
from .challenge import HumanHandoff
from .crawler import DEFAULT_MAX_DELAY, DEFAULT_MIN_DELAY, Pacing
from .expand import FollowPolicy
from .explain import explain
from .history import advise
from .models import AuthorRequest, SearchRequest
from .modes import check_environment, forget_state, rehearse_takeover, self_check, show_state
from .plan import RunPlan, plan_run
from .recipes import getting_started, render
from .run import CrawlLimits, Outputs, crawl
from .storage import ChallengeLog
from .urls import SCHOLAR_HOST, parse_cluster_id, parse_user_id


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line interface.

    :returns: the configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="scholar-crawler",
        description="Search Google Scholar in a real browser; hand the window to a human on CAPTCHA.",
        epilog=(
            "Most of these flags exist to be left alone. Run --recipes for complete commands "
            "to copy,\nand --dry-run to see what a run would cost before it starts."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    query = parser.add_argument_group("query")
    query.add_argument("-q", "--query", action="append", default=[], help="search query; repeatable")
    query.add_argument("--queries-file", type=Path, help="file with one query per line")
    query.add_argument(
        "--cites",
        action="append",
        default=[],
        help="list works citing this cluster; accepts an id or a cited_by_url; repeatable",
    )
    query.add_argument(
        "--cluster",
        action="append",
        default=[],
        help="list all versions of one work; accepts an id or a versions_url; repeatable",
    )
    query.add_argument(
        "--clusters-file",
        type=Path,
        help="file with one cluster id or versions_url per line, as written by "
        "scholar-digest --refresh-list",
    )
    query.add_argument(
        "--author",
        action="append",
        default=[],
        help="crawl this author profile; accepts a user id or a profile URL; repeatable",
    )
    query.add_argument(
        "--recipes",
        action="store_true",
        help="print ready-to-run commands for the usual tasks and stop",
    )
    query.add_argument(
        "--doctor",
        action="store_true",
        help="check that this machine can run a crawl — Python, libraries, a browser, "
        "writable directories — and print how to fix what cannot; sends no request",
    )
    query.add_argument(
        "--self-check",
        action="store_true",
        help="fetch one page for a fixed query and report whether every parsed field still "
        "arrives; use it to tell a Scholar layout change from a bug",
    )
    query.add_argument(
        "--show-state",
        action="store_true",
        help="list the resume progress stored in the state file and stop",
    )
    query.add_argument(
        "--forget",
        metavar="PATTERN",
        help="drop stored resume progress for targets whose signature contains PATTERN "
        "(an empty pattern drops all of it) and stop",
    )
    query.add_argument(
        "--dry-run",
        action="store_true",
        help="print what the run would request and how long it would take, then stop "
        "without sending anything",
    )
    query.add_argument(
        "--rehearse-handoff",
        action="store_true",
        help="rehearse the human takeover on a local page (no request to Google): the "
        "challenge is detected, the window is handed over, and the crawl resumes",
    )
    query.add_argument("--year-from", type=int, help="earliest publication year")
    query.add_argument("--year-to", type=int, help="latest publication year")
    query.add_argument("--lang", default="en", help="Scholar interface language (hl), default en")
    query.add_argument("--sort-by-date", action="store_true", help="sort by date instead of relevance")
    query.add_argument("--no-citations", action="store_true", help="exclude citation-only records")
    query.add_argument("--no-patents", action="store_true", help="exclude patents")
    query.add_argument("--review-only", action="store_true", help="review articles only")

    paging = parser.add_argument_group("paging")
    paging.add_argument(
        "-p", "--pages", type=int, default=3, help="pages per query (10 results each), default 3"
    )
    paging.add_argument("-n", "--max-results", type=int, help="stop each query after this many results")
    paging.add_argument("--start", type=int, default=0, help="first result offset, default 0")
    paging.add_argument(
        "--follow-cites",
        type=int,
        default=0,
        metavar="DEPTH",
        help="after the seed listings, crawl the works citing them, this many levels deep; "
        "each level multiplies requests, so keep it small",
    )
    paging.add_argument(
        "--follow-breadth",
        type=int,
        default=5,
        metavar="N",
        help="records expanded per level, most-cited first (default: 5)",
    )
    paging.add_argument(
        "--follow-min-citations",
        type=int,
        default=0,
        metavar="N",
        help="skip expanding records cited fewer times than this",
    )
    paging.add_argument("--resume", action="store_true", help="continue each query from the saved cursor")
    paging.add_argument("--host", default=SCHOLAR_HOST, help="Scholar host, e.g. https://scholar.google.de")

    output = parser.add_argument_group("output")
    output.add_argument("-o", "--out", type=Path, default=Path("out/results.jsonl"), help="JSONL output path")
    output.add_argument("--csv", type=Path, help="also export collected records to this CSV path")
    output.add_argument("--state", type=Path, default=Path("out/state.json"), help="resume-state path")
    output.add_argument(
        "--challenge-log",
        type=Path,
        default=Path("out/challenges.jsonl"),
        help="append every human takeover here (session material redacted); "
        "read it back with --show-state",
    )
    output.add_argument(
        "--bibtex",
        type=Path,
        help="also export BibTeX entries to this .bib file; costs two extra requests "
        "per record, so expect a slower run and more challenges",
    )
    output.add_argument(
        "--profiles-out",
        type=Path,
        default=Path("out/profiles.jsonl"),
        help="author profile headers (one record per author)",
    )
    output.add_argument("--dump-html", type=Path, help="save every fetched page's HTML here for debugging")
    output.add_argument(
        "--explain",
        action="store_true",
        help="read this command back in plain words — what it will crawl, which files it "
        "will touch, and which flags contradict each other — and stop",
    )

    browser = parser.add_argument_group("browser")
    browser.add_argument(
        "--profile", type=Path, default=Path(".scholar-profile"), help="persistent profile dir"
    )
    browser.add_argument("--headless", action="store_true", help="no window; a challenge then aborts the run")
    browser.add_argument(
        "--channel", default="chrome", help="browser channel; empty string uses bundled Chromium"
    )
    browser.add_argument("--locale", default="en-US", help="browser locale")
    browser.add_argument("--timezone", default="America/Los_Angeles", help="IANA timezone")
    browser.add_argument("--proxy", help="proxy server URL")
    browser.add_argument("--slow-mo", type=float, default=0.0, help="ms delay per browser action")

    pace = parser.add_argument_group("pacing and handoff")
    # Left unset so the run can tell an explicit choice from the default and never widen
    # a rhythm the user asked for; see _resolve_pacing.
    pace.add_argument(
        "--min-delay", type=float, help=f"min seconds between page requests (default: {DEFAULT_MIN_DELAY})"
    )
    pace.add_argument(
        "--max-delay", type=float, help=f"max seconds between page requests (default: {DEFAULT_MAX_DELAY})"
    )
    pace.add_argument(
        "--no-learn-from-history",
        action="store_true",
        help="ignore the challenge log instead of starting slower after previous blocks",
    )
    pace.add_argument(
        "--nav-timeout",
        type=float,
        default=45.0,
        metavar="SECONDS",
        help="give up on one page load after this long (default: 45)",
    )
    pace.add_argument("--cooldown-every", type=int, default=10, help="long pause every N pages; 0 disables")
    pace.add_argument("--cooldown-seconds", type=float, default=90.0, help="length of the long pause")
    pace.add_argument(
        "--handoff-timeout", type=float, default=600.0, help="seconds to wait for a human; 0 waits forever"
    )
    pace.add_argument("--max-handoffs", type=int, default=5, help="abort after this many takeovers")
    pace.add_argument(
        "--challenge-cooldown",
        type=float,
        default=300.0,
        metavar="SECONDS",
        help="wait this long before resuming when challenges arrive back to back "
        "(default: 300, 0 disables)",
    )
    pace.add_argument(
        "--backoff-factor",
        type=float,
        default=1.6,
        help="multiply page delays by this after each takeover; 1.0 keeps the rhythm",
    )
    return parser


def _lines_of(path: Path) -> list[str]:
    """Read a list file, dropping blank lines and ``#`` comments.

    :param path: the file to read.
    :returns: the remaining lines, stripped.
    """
    kept = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            kept.append(stripped)
    return kept


def _collect_queries(args: argparse.Namespace) -> list[str]:
    """Gather queries from ``--query`` flags and ``--queries-file``.

    :param args: parsed arguments.
    :returns: queries in the order given, blank lines and ``#`` comments dropped.
    """
    return list(args.query) + (_lines_of(args.queries_file) if args.queries_file else [])


def _collect_clusters(args: argparse.Namespace) -> list[str]:
    """Gather cluster ids from ``--cluster`` flags and ``--clusters-file``.

    :param args: parsed arguments.
    :returns: ids or URLs in the order given, blank lines and ``#`` comments dropped.
    """
    return list(args.cluster) + (_lines_of(args.clusters_file) if args.clusters_file else [])


def build_targets(args: argparse.Namespace) -> tuple[list[SearchRequest], list[AuthorRequest]]:
    """Turn parsed arguments into the listings and profiles to crawl, in the order given.

    :param args: parsed arguments.
    :returns: keyword/citation listings, and author profiles.
    :raises ValueError: when no entry point was given, or an id cannot be parsed.
    """
    shared = {
        "year_low": args.year_from,
        "year_high": args.year_to,
        "language": args.lang,
        "sort_by_date": args.sort_by_date,
        "include_citations": not args.no_citations,
        "include_patents": not args.no_patents,
        "review_only": args.review_only,
    }
    listings = [SearchRequest(query=query, **shared) for query in _collect_queries(args)]
    listings += [SearchRequest(cites=parse_cluster_id(value), **shared) for value in args.cites]
    listings += [
        SearchRequest(cluster=parse_cluster_id(value), **shared) for value in _collect_clusters(args)
    ]
    authors = [
        AuthorRequest(
            user_id=parse_user_id(value), language=args.lang, sort_by_year=args.sort_by_date
        )
        for value in args.author
    ]
    if not listings and not authors:
        raise ValueError(
            "provide at least one --query, --queries-file, --cites, --cluster, "
            "--clusters-file or --author"
        )
    return listings, authors


def filter_template(args: argparse.Namespace) -> SearchRequest:
    """Build a request carrying only the filters, used as the template for expansion.

    :param args: parsed arguments.
    :returns: a ``cites`` request whose id is replaced for each expanded record.
    """
    return SearchRequest(
        cites="0",
        year_low=args.year_from,
        year_high=args.year_to,
        language=args.lang,
        sort_by_date=args.sort_by_date,
        include_citations=not args.no_citations,
        include_patents=not args.no_patents,
        review_only=args.review_only,
    )


def _browser_options(args: argparse.Namespace) -> BrowserOptions:
    """Collect the browser flags into launch options.

    :param args: parsed arguments.
    :returns: the launch options for this run.
    """
    return BrowserOptions(
        user_data_dir=args.profile,
        headless=args.headless,
        channel=args.channel or None,
        locale=args.locale,
        timezone=args.timezone,
        proxy_server=args.proxy,
        slow_mo=args.slow_mo,
    )


def _session_of(args: argparse.Namespace) -> Session:
    """Collect the browser-backed settings the offline modes share with a crawl.

    :param args: parsed arguments.
    :returns: the session settings.
    """
    return Session(
        options=_browser_options(args),
        handoff=HumanHandoff(timeout=args.handoff_timeout, headless=args.headless),
        log=ChallengeLog(args.challenge_log),
        host=args.host,
        max_handoffs=args.max_handoffs,
        dump_dir=args.dump_html,
        language=args.lang,
    )


def _resolve_pacing(args: argparse.Namespace) -> Pacing:
    """Choose the run's rhythm, learning from previous blocks unless it was set by hand.

    Explicit ``--min-delay``/``--max-delay`` values are honoured as given; only the defaults
    are widened, and only when the challenge log records blocks.

    :param args: parsed arguments.
    :returns: the pacing this run should use.
    :raises ValueError: when the pacing values are inconsistent.
    """
    chosen = args.min_delay is not None or args.max_delay is not None
    min_delay = args.min_delay if args.min_delay is not None else DEFAULT_MIN_DELAY
    max_delay = args.max_delay if args.max_delay is not None else DEFAULT_MAX_DELAY
    if not args.no_learn_from_history:
        advice = advise(ChallengeLog(args.challenge_log).entries(), min_delay, max_delay)
        if advice is not None and chosen:
            print(f"[pace] {advice.history.describe()}", flush=True)
            if advice.changes_pacing:
                print(
                    "[pace] keeping the delays you passed; drop --min-delay/--max-delay "
                    "to let the log choose them",
                    flush=True,
                )
        elif advice is not None:
            print(f"[pace] {advice.describe()}", flush=True)
            if advice.changes_pacing:
                min_delay, max_delay = advice.min_delay, advice.max_delay
    return Pacing(
        min_delay=min_delay,
        max_delay=max_delay,
        cooldown_every=args.cooldown_every,
        cooldown_seconds=args.cooldown_seconds,
        backoff_factor=args.backoff_factor,
        challenge_cooldown=args.challenge_cooldown,
        nav_timeout=args.nav_timeout,
    )


def _limits_of(args: argparse.Namespace) -> CrawlLimits:
    """Collect the paging flags into limits for the run.

    :param args: parsed arguments.
    :returns: the paging limits this run should honour.
    """
    return CrawlLimits(
        pages=args.pages,
        start=args.start,
        resume=args.resume,
        max_results=args.max_results,
        lang=args.lang,
    )


def _run_offline_mode(args: argparse.Namespace) -> int | None:
    """Run whichever mode needs no crawl, if one was asked for.

    :param args: parsed arguments.
    :returns: the exit code of that mode, or None when a crawl should run.
    """
    if args.recipes:
        for line in render():
            print(line, flush=True)
        return 0
    if args.show_state:
        return show_state(args.state, args.challenge_log)
    if args.forget is not None:
        return forget_state(args.state, args.forget)
    if args.doctor:
        return check_environment(
            profile=args.profile, out=args.out, state=args.state, channel=args.channel or None
        )
    if args.self_check:
        return self_check(_session_of(args))
    if args.rehearse_handoff:
        return rehearse_takeover(_session_of(args))
    return None


def _plan_of(
    args: argparse.Namespace,
    listings: list[SearchRequest],
    authors: list[AuthorRequest],
    follow: FollowPolicy,
    pacing: Pacing,
) -> RunPlan:
    """Cost the run the arguments describe.

    :param args: parsed arguments.
    :param listings: seed listings.
    :param authors: seed profiles.
    :param follow: expansion policy.
    :param pacing: request rhythm.
    :returns: the planned cost.
    """
    return plan_run(
        listings,
        authors,
        pages=args.pages,
        max_results=args.max_results,
        follow=follow,
        bibtex=bool(args.bibtex),
        pacing=pacing,
        host=args.host,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the crawler from command-line arguments.

    :param argv: argument vector; defaults to ``sys.argv[1:]``.
    :returns: process exit code — 0 on success, 1 on usage or crawl failure, 130 on Ctrl+C.
    """
    given = argv if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(argv)
    offline = _run_offline_mode(args)
    if offline is not None:
        return offline
    try:
        listings, authors = build_targets(args)
        follow = FollowPolicy(
            depth=args.follow_cites,
            breadth=args.follow_breadth,
            min_citations=args.follow_min_citations,
        )
        pacing = _resolve_pacing(args)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        if not given:
            print("\nStart from one of these, or see --recipes:\n", file=sys.stderr)
            for line in getting_started():
                print(line, file=sys.stderr)
        return 1

    if args.explain:
        for line in explain(args, listings, authors, follow, pacing):
            print(f"[explain] {line}" if line else "[explain]", flush=True)
        if not args.dry_run:
            print("[explain] nothing was requested; drop --explain to start", flush=True)
            return 0
    if args.dry_run:
        for line in _plan_of(args, listings, authors, follow, pacing).render():
            print(f"[plan] {line}", flush=True)
        print("[plan] nothing was requested; drop --dry-run to start", flush=True)
        return 0

    outputs = Outputs.open_for(
        out=args.out,
        state=args.state,
        profiles=args.profiles_out,
        bibtex=args.bibtex,
        csv=args.csv,
    )
    if outputs.bibtex is not None and authors:
        print(
            "[bibtex] profile publications need their card id resolved first, "
            "so each one costs three page loads instead of two",
            flush=True,
        )
    if follow.enabled:
        print(
            f"[follow] depth {follow.depth} x breadth {follow.breadth}: up to "
            f"{follow.estimate(len(listings) + len(authors))} listings this run",
            flush=True,
        )
    return crawl(
        _session_of(args),
        pacing,
        _limits_of(args),
        listings,
        authors,
        follow,
        filter_template(args),
        outputs,
    )


if __name__ == "__main__":
    raise SystemExit(main())
