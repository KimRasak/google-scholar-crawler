"""Executing one crawl: paging targets, expanding the graph, writing every output.

The command line owns argument parsing; this module owns what a run actually does, so a
crawl can be driven — and tested — without argparse.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .browser import Session, browser_session
from .challenge import ChallengeUnattended
from .crawler import Pacing, ScholarCrawler
from .expand import FollowPolicy, next_level
from .models import AuthorProfile, AuthorRequest, ScholarResult, SearchRequest
from .parser import bibtex_key
from .storage import BibtexSink, ChallengeLog, ProfileStore, ResultSink, StateStore


@dataclass(slots=True, frozen=True)
class CrawlLimits:
    """How far a run pages, and in which interface language.

    :param pages: pages per listing, or profile batches per profile.
    :param start: result offset to begin at when not resuming.
    :param resume: continue each target from its stored cursor.
    :param max_results: stop a target after this many records, when set.
    :param lang: Scholar interface language, which BibTeX export follows.
    """

    pages: int = 1
    start: int = 0
    resume: bool = False
    max_results: int | None = None
    lang: str = "en"


def report_page(page_start: int, parsed: int, new: int, total: str) -> None:
    """Print one progress line for a fetched page.

    :param page_start: result offset of the page.
    :param parsed: records parsed from the page.
    :param new: records that were not already stored.
    :param total: result-count estimate rendered for display.
    """
    print(f"[page] offset={page_start} parsed={parsed} new={new} total={total}", flush=True)


def crawl_listing(
    crawler: ScholarCrawler,
    request: SearchRequest,
    limits: CrawlLimits,
    sink: ResultSink,
    state: StateStore,
    bibtex: BibtexSink | None = None,
    depth: int = 0,
) -> list[ScholarResult]:
    """Crawl one keyword, citation or version listing into ``sink``.

    :param crawler: the bound crawler.
    :param request: the listing to page through.
    :param limits: paging limits for this run.
    :param sink: JSONL writer for parsed records.
    :param state: resume cursor store.
    :param bibtex: when set, each record's BibTeX entry is exported as well.
    :param depth: citation-graph level this listing came from, recorded on every record.
    :returns: every record parsed from this listing, for the next expansion level.
    """
    signature = request.signature()
    start = state.next_start(signature, limits.start) if limits.resume else limits.start
    label = f"[query] {request.label!r} from offset {start}"
    print(f"\n{label}" + (f" (level {depth})" if depth else ""), flush=True)
    collected: list[ScholarResult] = []
    for page in crawler.search(
        request, max_pages=limits.pages, start=start, max_results=limits.max_results
    ):
        for result in page.results:
            result.extra["follow_depth"] = depth
        if bibtex is not None:
            export_bibtex(crawler, page.results, limits, bibtex)
        new = sum(1 for result in page.results if sink.write(result))
        total = f"~{page.total_estimate}" if page.total_estimate else "unknown"
        report_page(page.start, len(page.results), new, total)
        finished = not page.has_next and not page.truncated
        state.record(signature, page.start + len(page.results), exhausted=finished)
        collected += page.results
    return collected


def export_bibtex(
    crawler: ScholarCrawler,
    results: list[ScholarResult],
    limits: CrawlLimits,
    bibtex: BibtexSink,
) -> None:
    """Fetch and store the BibTeX entry of every result that has a cluster id.

    The citation key is recorded on the record as ``extra.bibtex_key`` so the JSONL and
    the ``.bib`` file can be joined.

    :param crawler: the bound crawler.
    :param results: records of one page, updated in place.
    :param limits: paging limits, supplying the interface language.
    :param bibtex: the ``.bib`` writer.
    """
    for result in results:
        entry = crawler.fetch_bibtex(result, language=limits.lang)
        if entry is None:
            continue
        bibtex.write(entry)
        result.extra["bibtex_key"] = bibtex_key(entry)


def crawl_author(
    crawler: ScholarCrawler,
    request: AuthorRequest,
    limits: CrawlLimits,
    sink: ResultSink,
    state: StateStore,
    profiles: ProfileStore,
    bibtex: BibtexSink | None = None,
) -> list[ScholarResult]:
    """Crawl one author profile into ``sink`` and its header into ``profiles``.

    :param crawler: the bound crawler.
    :param request: the profile to read.
    :param limits: paging limits for this run.
    :param sink: JSONL writer for the publication records.
    :param state: resume cursor store.
    :param profiles: writer for the profile header record.
    :param bibtex: when set, each publication's BibTeX entry is exported as well.
    :returns: every publication record parsed, for the next expansion level.
    """
    signature = request.signature()
    start = state.next_start(signature, limits.start) if limits.resume else limits.start
    print(f"\n[author] {request.user_id} from publication {start}", flush=True)
    latest: AuthorProfile | None = None
    collected: list[ScholarResult] = []
    for batch in crawler.crawl_author(
        request, max_pages=limits.pages, cstart=start, max_results=limits.max_results
    ):
        latest = batch.profile
        collected += batch.results
        profiles.write(batch.profile)
        if bibtex is not None:
            export_bibtex(crawler, batch.results, limits, bibtex)
        new = sum(1 for result in batch.results if sink.write(result))
        report_page(batch.cstart, len(batch.results), new, f"~{batch.profile.cited_by_total} citations")
        finished = not batch.has_more and not batch.truncated
        state.record(signature, batch.cstart + len(batch.results), exhausted=finished)
    if latest is not None:
        print(
            f"[author] {latest.name or request.user_id}: {latest.cited_by_total} citations, "
            f"h-index {latest.h_index} -> {profiles.path}",
            flush=True,
        )
    return collected


def follow_citations(
    crawler: ScholarCrawler,
    seeds: list[ScholarResult],
    limits: CrawlLimits,
    policy: FollowPolicy,
    template: SearchRequest,
    sink: ResultSink,
    state: StateStore,
    bibtex: BibtexSink | None,
) -> None:
    """Walk the citation graph outward from records already collected.

    :param crawler: the bound crawler.
    :param seeds: records collected from the seed listings and profiles.
    :param limits: paging limits for this run.
    :param policy: how deep and how wide to expand.
    :param template: the filters every generated citation listing inherits.
    :param sink: JSONL writer for parsed records.
    :param state: resume cursor store.
    :param bibtex: when set, each record's BibTeX entry is exported as well.
    """
    if not policy.enabled:
        return
    visited: set[str] = set()
    frontier = seeds
    for level in range(1, policy.depth + 1):
        requests = next_level(frontier, template, policy, visited)
        if not requests:
            print(f"[follow] level {level}: nothing left to expand", flush=True)
            return
        print(f"[follow] level {level}: {len(requests)} citation listings", flush=True)
        frontier = []
        for request in requests:
            frontier += crawl_listing(crawler, request, limits, sink, state, bibtex, depth=level)


@dataclass(slots=True)
class Outputs:
    """The files one crawl writes, opened together and reported together.

    :param sink: JSONL writer for parsed records.
    :param state: resume cursor store.
    :param profiles: author-profile store.
    :param bibtex: BibTeX writer, when export was asked for.
    :param challenges: takeover log, when a path was given.
    :param csv_path: CSV destination written when the run ends, when asked for.
    """

    sink: ResultSink
    state: StateStore
    profiles: ProfileStore
    bibtex: BibtexSink | None
    challenges: ChallengeLog | None = None
    csv_path: Path | None = None

    @classmethod
    def open_for(
        cls,
        *,
        out: Path,
        state: Path,
        profiles: Path,
        bibtex: Path | None = None,
        csv: Path | None = None,
        challenges: Path | None = None,
    ) -> Outputs:
        """Open every output file a run writes.

        :param out: JSONL destination for parsed records.
        :param state: resume-cursor file.
        :param profiles: author-profile file.
        :param bibtex: ``.bib`` destination, when BibTeX export was asked for.
        :param csv: CSV destination written once the run ends, when asked for.
        :param challenges: destination for the takeover log, when asked for.
        :returns: the opened outputs.
        """
        sink = ResultSink(out)
        sink.open()
        cursors = StateStore(state)
        cursors.load()
        store = ProfileStore(profiles)
        store.load()
        entries = BibtexSink(bibtex) if bibtex else None
        if entries is not None:
            entries.open()
        return cls(
            sink=sink,
            state=cursors,
            profiles=store,
            bibtex=entries,
            challenges=ChallengeLog(challenges) if challenges else None,
            csv_path=csv,
        )

    def close_and_report(self, crawler: ScholarCrawler | None) -> None:
        """Close every file and print what was written.

        Runs whether the crawl finished, failed or was interrupted, so an aborted run still
        reports its output and its request cost.

        :param crawler: the crawler that ran, or None when the browser never opened.
        """
        self.sink.close()
        if self.csv_path:
            print(f"[out] {self.sink.export_csv(self.csv_path)} rows -> {self.csv_path}", flush=True)
        print(
            f"[out] {self.sink.written} new records "
            f"({self.sink.skipped} duplicates skipped) -> {self.sink.path}",
            flush=True,
        )
        for line in self.sink.tally.describe_alarms():
            print(f"[audit] {line}", flush=True)
        if self.bibtex is not None:
            self.bibtex.close()
            print(
                f"[out] {self.bibtex.written} BibTeX entries "
                f"({self.bibtex.skipped} duplicates skipped) -> {self.bibtex.path}",
                flush=True,
            )
        if self.profiles.written:
            print(f"[out] {self.profiles.written} profile updates -> {self.profiles.path}", flush=True)
        if crawler is not None:
            print(f"[run] {crawler.stats().render()}", flush=True)


def crawl_targets(
    crawler: ScholarCrawler,
    limits: CrawlLimits,
    listings: list[SearchRequest],
    authors: list[AuthorRequest],
    follow: FollowPolicy,
    template: SearchRequest,
    outputs: Outputs,
) -> None:
    """Crawl every seed target, then expand along the citation graph.

    :param crawler: the bound crawler.
    :param limits: paging limits for this run.
    :param listings: seed listings.
    :param authors: seed profiles.
    :param follow: expansion policy.
    :param template: the filters generated citation listings inherit.
    :param outputs: the opened output files.
    """
    sink, state, bibtex = outputs.sink, outputs.state, outputs.bibtex
    harvest: list[ScholarResult] = []
    for listing in listings:
        harvest += crawl_listing(crawler, listing, limits, sink, state, bibtex)
    for author in authors:
        harvest += crawl_author(crawler, author, limits, sink, state, outputs.profiles, bibtex)
    follow_citations(crawler, harvest, limits, follow, template, sink, state, bibtex)


def crawl(
    session: Session,
    pacing: Pacing,
    limits: CrawlLimits,
    listings: list[SearchRequest],
    authors: list[AuthorRequest],
    follow: FollowPolicy,
    template: SearchRequest,
    outputs: Outputs,
) -> int:
    """Open the browser and crawl every target, reporting the run whatever happens.

    Interruption and failure are ordinary endings here: whatever was collected has already
    been written, so the outputs are closed and the run summary is printed either way.

    :param session: browser settings and the takeover policy.
    :param pacing: request rhythm.
    :param limits: paging limits for this run.
    :param listings: seed listings.
    :param authors: seed profiles.
    :param follow: expansion policy.
    :param template: the filters generated citation listings inherit.
    :param outputs: the opened output files.
    :returns: process exit code — 0 on success, 1 on a crawl failure, 130 on Ctrl+C.
    """
    exit_code = 0
    crawler: ScholarCrawler | None = None
    try:
        with browser_session(session.options) as (_context, page):
            crawler = ScholarCrawler(
                page,
                session.handoff,
                pacing,
                host=session.host,
                max_handoffs=session.max_handoffs,
                dump_dir=session.dump_dir,
                challenge_log=session.log,
            )
            crawl_targets(crawler, limits, listings, authors, follow, template, outputs)
    except KeyboardInterrupt:
        print("\n[stop] interrupted by user", flush=True)
        exit_code = 130
    except (ChallengeUnattended, RuntimeError) as error:
        print(f"\n[stop] {error}", file=sys.stderr)
        exit_code = 1
    finally:
        outputs.close_and_report(crawler)
    return exit_code
