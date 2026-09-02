"""Command-line entry point for the Scholar crawler."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .browser import BrowserOptions, browser_session
from .challenge import ChallengeUnattended, HumanHandoff
from .crawler import Pacing, ScholarCrawler
from .models import SearchRequest
from .storage import ResultSink, StateStore
from .urls import SCHOLAR_HOST


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
    query.add_argument("--year-from", type=int, help="earliest publication year")
    query.add_argument("--year-to", type=int, help="latest publication year")
    query.add_argument("--lang", default="en", help="Scholar interface language (hl), default en")
    query.add_argument("--sort-by-date", action="store_true", help="sort by date instead of relevance")
    query.add_argument("--no-citations", action="store_true", help="exclude citation-only records")
    query.add_argument("--no-patents", action="store_true", help="exclude patents")
    query.add_argument("--review-only", action="store_true", help="review articles only")

    paging = parser.add_argument_group("paging")
    paging.add_argument("-p", "--pages", type=int, default=3, help="pages per query (10 results each), default 3")
    paging.add_argument("--start", type=int, default=0, help="first result offset, default 0")
    paging.add_argument("--resume", action="store_true", help="continue each query from the saved cursor")
    paging.add_argument("--host", default=SCHOLAR_HOST, help="Scholar host, e.g. https://scholar.google.de")

    output = parser.add_argument_group("output")
    output.add_argument("-o", "--out", type=Path, default=Path("out/results.jsonl"), help="JSONL output path")
    output.add_argument("--csv", type=Path, help="also export collected records to this CSV path")
    output.add_argument("--state", type=Path, default=Path("out/state.json"), help="resume-state path")

    browser = parser.add_argument_group("browser")
    browser.add_argument("--profile", type=Path, default=Path(".scholar-profile"), help="persistent profile dir")
    browser.add_argument("--headless", action="store_true", help="no window; a challenge then aborts the run")
    browser.add_argument("--channel", default="chrome", help="browser channel; empty string uses bundled Chromium")
    browser.add_argument("--locale", default="en-US", help="browser locale")
    browser.add_argument("--timezone", default="America/Los_Angeles", help="IANA timezone")
    browser.add_argument("--proxy", help="proxy server URL")
    browser.add_argument("--slow-mo", type=float, default=0.0, help="ms delay per browser action")

    pace = parser.add_argument_group("pacing and handoff")
    pace.add_argument("--min-delay", type=float, default=4.0, help="min seconds between page requests")
    pace.add_argument("--max-delay", type=float, default=11.0, help="max seconds between page requests")
    pace.add_argument("--cooldown-every", type=int, default=10, help="long pause every N pages; 0 disables")
    pace.add_argument("--cooldown-seconds", type=float, default=90.0, help="length of the long pause")
    pace.add_argument("--handoff-timeout", type=float, default=600.0, help="seconds to wait for a human; 0 waits forever")
    pace.add_argument("--max-handoffs", type=int, default=5, help="abort after this many takeovers")
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


def main(argv: list[str] | None = None) -> int:
    """Run the crawler from command-line arguments.

    :param argv: argument vector; defaults to ``sys.argv[1:]``.
    :returns: process exit code — 0 on success, 1 on usage or crawl failure, 130 on Ctrl+C.
    """
    args = build_parser().parse_args(argv)
    queries = _collect_queries(args)
    if not queries:
        print("error: provide at least one --query or a --queries-file", file=sys.stderr)
        return 1

    requests = [
        SearchRequest(
            query=query,
            year_low=args.year_from,
            year_high=args.year_to,
            language=args.lang,
            sort_by_date=args.sort_by_date,
            include_citations=not args.no_citations,
            include_patents=not args.no_patents,
            review_only=args.review_only,
        )
        for query in queries
    ]

    sink = ResultSink(args.out)
    sink.open()
    state = StateStore(args.state)
    state.load()
    options = BrowserOptions(
        user_data_dir=args.profile,
        headless=args.headless,
        channel=args.channel or None,
        locale=args.locale,
        timezone=args.timezone,
        proxy_server=args.proxy,
        slow_mo=args.slow_mo,
    )
    pacing = Pacing(
        min_delay=args.min_delay,
        max_delay=args.max_delay,
        cooldown_every=args.cooldown_every,
        cooldown_seconds=args.cooldown_seconds,
    )
    handoff = HumanHandoff(timeout=args.handoff_timeout, headless=args.headless)
    exit_code = 0
    try:
        with browser_session(options) as (_context, page):
            crawler = ScholarCrawler(page, handoff, pacing, host=args.host, max_handoffs=args.max_handoffs)
            for request in requests:
                signature = request.signature()
                start = state.next_start(signature, args.start) if args.resume else args.start
                print(f"\n[query] {request.query!r} from offset {start}", flush=True)
                for page_result in crawler.search(request, max_pages=args.pages, start=start):
                    new = sum(1 for result in page_result.results if sink.write(result))
                    total = f"~{page_result.total_estimate}" if page_result.total_estimate else "unknown"
                    print(
                        f"[page] offset={page_result.start} parsed={len(page_result.results)} "
                        f"new={new} total={total}",
                        flush=True,
                    )
                    offset = page_result.start + len(page_result.results)
                    state.record(signature, offset, exhausted=not page_result.has_next)
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
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
