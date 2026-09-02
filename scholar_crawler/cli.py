"""Command-line entry point for the Scholar crawler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .browser import BrowserOptions, browser_session
from .challenge import ChallengeUnattended, HumanHandoff
from .crawler import Pacing, ScholarCrawler
from .models import AuthorProfile, AuthorRequest, ScholarResult, SearchRequest
from .parser import bibtex_key
from .storage import BibtexSink, ProfileStore, ResultSink, StateStore
from .urls import SCHOLAR_HOST, parse_cluster_id, parse_user_id


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line interface.

    :returns: the configured argument parser.
    """
    parser = argparse.ArgumentParser(
        prog="scholar-crawler",
        description="Search Google Scholar in a real browser; hand the window to a human on CAPTCHA.",
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
        "--author",
        action="append",
        default=[],
        help="crawl this author profile; accepts a user id or a profile URL; repeatable",
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
    paging.add_argument("--resume", action="store_true", help="continue each query from the saved cursor")
    paging.add_argument("--host", default=SCHOLAR_HOST, help="Scholar host, e.g. https://scholar.google.de")

    output = parser.add_argument_group("output")
    output.add_argument("-o", "--out", type=Path, default=Path("out/results.jsonl"), help="JSONL output path")
    output.add_argument("--csv", type=Path, help="also export collected records to this CSV path")
    output.add_argument("--state", type=Path, default=Path("out/state.json"), help="resume-state path")
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
    pace.add_argument("--min-delay", type=float, default=4.0, help="min seconds between page requests")
    pace.add_argument("--max-delay", type=float, default=11.0, help="max seconds between page requests")
    pace.add_argument("--cooldown-every", type=int, default=10, help="long pause every N pages; 0 disables")
    pace.add_argument("--cooldown-seconds", type=float, default=90.0, help="length of the long pause")
    pace.add_argument(
        "--handoff-timeout", type=float, default=600.0, help="seconds to wait for a human; 0 waits forever"
    )
    pace.add_argument("--max-handoffs", type=int, default=5, help="abort after this many takeovers")
    pace.add_argument(
        "--backoff-factor",
        type=float,
        default=1.6,
        help="multiply page delays by this after each takeover; 1.0 keeps the rhythm",
    )
    return parser


def _collect_queries(args: argparse.Namespace) -> list[str]:
    """Gather queries from ``--query`` flags and ``--queries-file``.

    :param args: parsed arguments.
    :returns: queries in the order given, blank lines and ``#`` comments dropped.
    """
    queries = list(args.query)
    if args.queries_file:
        for line in args.queries_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                queries.append(line)
    return queries


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
    listings += [SearchRequest(cluster=parse_cluster_id(value), **shared) for value in args.cluster]
    authors = [
        AuthorRequest(
            user_id=parse_user_id(value), language=args.lang, sort_by_year=args.sort_by_date
        )
        for value in args.author
    ]
    if not listings and not authors:
        raise ValueError(
            "provide at least one --query, --queries-file, --cites, --cluster or --author"
        )
    return listings, authors


def _report(page_start: int, parsed: int, new: int, total: str) -> None:
    """Print one progress line for a fetched page.

    :param page_start: result offset of the page.
    :param parsed: records parsed from the page.
    :param new: records that were not already stored.
    :param total: result-count estimate rendered for display.
    """
    print(f"[page] offset={page_start} parsed={parsed} new={new} total={total}", flush=True)


def _crawl_listing(
    crawler: ScholarCrawler,
    request: SearchRequest,
    args: argparse.Namespace,
    sink: ResultSink,
    state: StateStore,
    bibtex: BibtexSink | None = None,
) -> None:
    """Crawl one keyword, citation or version listing into ``sink``.

    :param crawler: the bound crawler.
    :param request: the listing to page through.
    :param args: parsed arguments supplying paging limits.
    :param sink: JSONL writer for parsed records.
    :param state: resume cursor store.
    :param bibtex: when set, each record's BibTeX entry is exported as well.
    """
    signature = request.signature()
    start = state.next_start(signature, args.start) if args.resume else args.start
    print(f"\n[query] {request.label!r} from offset {start}", flush=True)
    for page in crawler.search(
        request, max_pages=args.pages, start=start, max_results=args.max_results
    ):
        if bibtex is not None:
            _export_bibtex(crawler, page.results, args, bibtex)
        new = sum(1 for result in page.results if sink.write(result))
        total = f"~{page.total_estimate}" if page.total_estimate else "unknown"
        _report(page.start, len(page.results), new, total)
        state.record(signature, page.start + len(page.results), exhausted=not page.has_next)


def _export_bibtex(
    crawler: ScholarCrawler,
    results: list[ScholarResult],
    args: argparse.Namespace,
    bibtex: BibtexSink,
) -> None:
    """Fetch and store the BibTeX entry of every result that has a cluster id.

    The citation key is recorded on the record as ``extra.bibtex_key`` so the JSONL and
    the ``.bib`` file can be joined.

    :param crawler: the bound crawler.
    :param results: records of one page, updated in place.
    :param args: parsed arguments supplying the interface language.
    :param bibtex: the ``.bib`` writer.
    """
    for result in results:
        entry = crawler.fetch_bibtex(result, language=args.lang)
        if entry is None:
            continue
        bibtex.write(entry)
        result.extra["bibtex_key"] = bibtex_key(entry)


def _crawl_author(
    crawler: ScholarCrawler,
    request: AuthorRequest,
    args: argparse.Namespace,
    sink: ResultSink,
    state: StateStore,
    profiles: ProfileStore,
) -> None:
    """Crawl one author profile into ``sink`` and its header into ``profiles``.

    :param crawler: the bound crawler.
    :param request: the profile to read.
    :param args: parsed arguments supplying paging limits.
    :param sink: JSONL writer for the publication records.
    :param state: resume cursor store.
    :param profiles: writer for the profile header record.
    """
    signature = request.signature()
    start = state.next_start(signature, args.start) if args.resume else args.start
    print(f"\n[author] {request.user_id} from publication {start}", flush=True)
    latest: AuthorProfile | None = None
    for batch in crawler.crawl_author(
        request, max_pages=args.pages, cstart=start, max_results=args.max_results
    ):
        latest = batch.profile
        profiles.write(batch.profile)
        new = sum(1 for result in batch.results if sink.write(result))
        _report(batch.cstart, len(batch.results), new, f"~{batch.profile.cited_by_total} citations")
        state.record(signature, batch.cstart + len(batch.results), exhausted=not batch.has_more)
    if latest is not None:
        print(
            f"[author] {latest.name or request.user_id}: {latest.cited_by_total} citations, "
            f"h-index {latest.h_index} -> {profiles.path}",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    """Run the crawler from command-line arguments.

    :param argv: argument vector; defaults to ``sys.argv[1:]``.
    :returns: process exit code — 0 on success, 1 on usage or crawl failure, 130 on Ctrl+C.
    """
    args = build_parser().parse_args(argv)
    try:
        listings, authors = build_targets(args)
        pacing = Pacing(
            min_delay=args.min_delay,
            max_delay=args.max_delay,
            cooldown_every=args.cooldown_every,
            cooldown_seconds=args.cooldown_seconds,
            backoff_factor=args.backoff_factor,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    sink = ResultSink(args.out)
    sink.open()
    state = StateStore(args.state)
    state.load()
    profiles = ProfileStore(args.profiles_out)
    profiles.load()
    bibtex = BibtexSink(args.bibtex) if args.bibtex else None
    if bibtex is not None:
        bibtex.open()
        if authors:
            print(
                "[bibtex] profile publications carry no cluster id; "
                "BibTeX is exported for keyword, --cites and --cluster records only",
                flush=True,
            )
    options = BrowserOptions(
        user_data_dir=args.profile,
        headless=args.headless,
        channel=args.channel or None,
        locale=args.locale,
        timezone=args.timezone,
        proxy_server=args.proxy,
        slow_mo=args.slow_mo,
    )
    handoff = HumanHandoff(timeout=args.handoff_timeout, headless=args.headless)
    exit_code = 0
    try:
        with browser_session(options) as (_context, page):
            crawler = ScholarCrawler(
                page,
                handoff,
                pacing,
                host=args.host,
                max_handoffs=args.max_handoffs,
                dump_dir=args.dump_html,
            )
            for listing in listings:
                _crawl_listing(crawler, listing, args, sink, state, bibtex)
            for author in authors:
                _crawl_author(crawler, author, args, sink, state, profiles)
    except KeyboardInterrupt:
        print("\n[stop] interrupted by user", flush=True)
        exit_code = 130
    except (ChallengeUnattended, RuntimeError) as error:
        print(f"\n[stop] {error}", file=sys.stderr)
        exit_code = 1
    finally:
        sink.close()
        if args.csv:
            rows = sink.export_csv(args.csv)
            print(f"[out] {rows} rows -> {args.csv}", flush=True)
        print(
            f"[out] {sink.written} new records ({sink.skipped} duplicates skipped) -> {sink.path}",
            flush=True,
        )
        if bibtex is not None:
            bibtex.close()
            print(
                f"[out] {bibtex.written} BibTeX entries "
                f"({bibtex.skipped} duplicates skipped) -> {bibtex.path}",
                flush=True,
            )
        if profiles.written:
            print(f"[out] {profiles.written} profile updates -> {profiles.path}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
