r"""Checking that the guards guard: break one invariant, expect one failing test.

A test that never fails is indistinguishable from a test that cannot fail. Two rounds of
hand-run mutations found three promises resting on nothing — the ``--min-citations``
threshold, the length of the staleness list, and the ``flush()`` behind "a crash never
loses collected data" — so the mutations are kept here instead of being rewritten each
time.

Each entry names a literal string in a source file, the wrong version of it, and the tests
that must notice. The string has to appear exactly once: a mutation that lands in a
docstring instead of the code it describes reports a hole that does not exist, which is how
two false negatives happened before this rule existed.

Run the fast set, or everything::

    python3 -m tests.mutate
    python3 -m tests.mutate --all
    python3 -m tests.mutate offset      # only entries whose label matches

The audit rewrites source files while it runs and restores them in a ``finally`` block, so
do not run it against a tree with uncommitted work you cannot recover.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

LOOP = "tests/test_end_to_end.py tests/test_crawler.py tests/test_state.py tests/test_challenge.py"
OFFLINE = (
    "tests/test_digest.py tests/test_collection.py tests/test_storage.py "
    "tests/test_refresh.py tests/test_bibsynth.py tests/test_audit.py"
)
DOCS = "tests/test_docs.py tests/test_recipes.py tests/test_interface.py"


@dataclass(frozen=True, slots=True)
class Mutation:
    """One deliberate defect and the tests expected to catch it.

    :param label: what the guarded invariant is, in the report.
    :param path: source file to edit, relative to the project root.
    :param original: literal text to replace; must occur exactly once in the file.
    :param broken: the wrong version of that text.
    :param tests: space-separated pytest files or node ids expected to fail.
    :param slow: True when the broken version makes a test wait out a real timeout.
    """

    label: str
    path: str
    original: str
    broken: str
    tests: str
    slow: bool = False


MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        "the page limit stops the run",
        "scholar_crawler/crawler.py",
        "for _page_index in range(max_pages):",
        "for _page_index in range(max_pages + 1):",
        LOOP,
    ),
    Mutation(
        "the offset advances by a whole page",
        "scholar_crawler/crawler.py",
        "offset += RESULTS_PER_PAGE",
        "offset += RESULTS_PER_PAGE - 1",
        LOOP,
    ),
    Mutation(
        "resume state records the first unfetched offset",
        "scholar_crawler/run.py",
        "state.record(signature, page.start + len(page.results), exhausted=finished)",
        "state.record(signature, page.start, exhausted=finished)",
        LOOP,
    ),
    Mutation(
        "the takeover budget aborts the run",
        "scholar_crawler/crawler.py",
        "if self.handoff_count > self._max_handoffs:",
        "if self.handoff_count > self._max_handoffs + 1000:",
        LOOP,
    ),
    Mutation(
        "a good page resets the consecutive-challenge count",
        "scholar_crawler/crawler.py",
        "            self.consecutive_handoffs = 0",
        "            pass",
        LOOP,
    ),
    Mutation(
        "the run stops when Scholar offers no next page",
        "scholar_crawler/crawler.py",
        "if page_result.truncated or not page_result.results or not page_result.has_next:",
        "if page_result.truncated or not page_result.results:",
        LOOP,
    ),
    Mutation(
        "the record cap truncates the last page exactly",
        "scholar_crawler/crawler.py",
        "page_result.results = page_result.results[: max_results - collected]",
        "page_result.results = page_result.results[: max_results - collected + 1]",
        f"{LOOP} tests/test_plan.py",
    ),
    Mutation(
        "an author batch advances by its own size",
        "scholar_crawler/crawler.py",
        "offset += AUTHOR_PAGE_SIZE",
        "offset += AUTHOR_PAGE_SIZE - 1",
        f"{LOOP} tests/test_author.py",
    ),
    Mutation(
        "the cooldown fires on its interval",
        "scholar_crawler/crawler.py",
        "if self.cooldown_every and page_index % self.cooldown_every == 0:",
        "if False and self.cooldown_every and page_index % self.cooldown_every == 0:",
        LOOP,
    ),
    Mutation(
        "the challenge cooldown grows with repeats",
        "scholar_crawler/crawler.py",
        "wait = self.challenge_cooldown * (consecutive - 1)",
        "wait = self.challenge_cooldown * 0",
        f"{LOOP} tests/test_crawler.py",
    ),
    Mutation(
        "a captcha selector takes part in detection",
        "scholar_crawler/challenge.py",
        '("#gs_captcha_ccl", ChallengeKind.CAPTCHA),',
        "",
        f"{LOOP} tests/test_challenge.py",
    ),
    Mutation(
        "the results selector still matches a real page",
        "scholar_crawler/challenge.py",
        'RESULTS_SELECTOR = "div.gs_r.gs_or.gs_scl',
        'RESULTS_SELECTOR = "div.gs_nope.gs_or.gs_scl',
        "tests/test_real_pages.py tests/test_end_to_end.py",
    ),
    Mutation(
        "headless refuses to wait for a human",
        "scholar_crawler/challenge.py",
        "        if self.headless:",
        "        if False and self.headless:",
        "tests/test_challenge.py tests/test_end_to_end.py",
        slow=True,
    ),
    Mutation(
        "the citation count comes from the cites link",
        "scholar_crawler/parser.py",
        'if "cites=" in href and "cluster=" not in href:',
        'if "citesX=" in href and "cluster=" not in href:',
        "tests/test_parser.py tests/test_real_pages.py",
    ),
    Mutation(
        "the writer drops a record it already wrote",
        "scholar_crawler/storage.py",
        "        if key in self._seen:",
        "        if False and key in self._seen:",
        f"{OFFLINE} tests/test_end_to_end.py",
    ),
    Mutation(
        "a record reaches the file before the run ends",
        "scholar_crawler/storage.py",
        'self._handle.write(json.dumps(payload, ensure_ascii=False) + "\\n")\n        self._handle.flush()',
        'self._handle.write(json.dumps(payload, ensure_ascii=False) + "\\n")',
        "tests/test_storage.py tests/test_end_to_end.py",
    ),
    Mutation(
        "a BibTeX entry reaches the file before the run ends",
        "scholar_crawler/storage.py",
        'self._handle.write(entry.strip() + "\\n\\n")\n        self._handle.flush()',
        'self._handle.write(entry.strip() + "\\n\\n")',
        "tests/test_bibtex.py tests/test_storage.py",
    ),
    Mutation(
        "dedup identity prefers the card id",
        "scholar_crawler/models.py",
        "    return cluster_id or f\"{title}::{link or ''}\"",
        "    return f\"{title}::{link or ''}\"",
        OFFLINE,
    ),
    Mutation(
        "a number stored as text is read as a number",
        "scholar_crawler/models.py",
        '            fixed[field_name] = int(value.strip())',
        "            pass",
        "tests/test_digest.py",
    ),
    Mutation(
        "a field of the wrong type does not reach the numbers",
        "scholar_crawler/models.py",
        "        else:\n            fixed[field_name] = None",
        "        else:\n            pass",
        "tests/test_digest.py",
    ),
    Mutation(
        "counts and groups collapse one venue spelled two ways",
        "scholar_crawler/analysis.py",
        "            display, members = grouped.setdefault(label.casefold(), (label, []))",
        "            display, members = grouped.setdefault(label, (label, []))",
        "tests/test_analysis.py tests/test_report.py",
    ),
    Mutation(
        "merging keeps the higher citation count",
        "scholar_crawler/digest.py",
        'return (record.get("cited_by_count") or -1, _filled(record))',
        "return (0, _filled(record))",
        OFFLINE,
    ),
    Mutation(
        "merging fills fields the winner lacks",
        "scholar_crawler/digest.py",
        '        if key != "extra" and merged.get(key) in (None, "", []):',
        '        if False and key != "extra" and merged.get(key) in (None, "", []):',
        OFFLINE,
    ),
    Mutation(
        "the citation threshold keeps records that meet it",
        "scholar_crawler/digest.py",
        'if (record.get("cited_by_count") or 0) < min_citations:',
        'if (record.get("cited_by_count") or 0) <= min_citations:',
        OFFLINE,
    ),
    Mutation(
        "the staleness report honours its limit",
        "scholar_crawler/refresh.py",
        "stale[:top]",
        "stale[: top + 1]",
        OFFLINE,
    ),
    Mutation(
        "a truncated author list becomes 'and others'",
        "scholar_crawler/bibsynth.py",
        'names.append("others")',
        'names.append("friends")',
        OFFLINE,
    ),
    Mutation(
        "an unknown setting in a settings file is refused",
        "scholar_crawler/config.py",
        'return ConfigError(f"{path}: unknown setting {name!r}{hint}")',
        "return None  # type: ignore[return-value]",
        "tests/test_config.py",
    ),
    Mutation(
        "--json puts nothing but the document on stdout",
        "scholar_crawler/machine.py",
        "with redirect_stdout(sys.stderr):",
        "with redirect_stdout(sys.stdout):",
        "tests/test_machine.py",
    ),
    Mutation(
        "the failure vocabulary matches AGENTS.md",
        "scholar_crawler/machine.py",
        '    "usage",\n)',
        '    "usage_x",\n)',
        "tests/test_machine.py",
    ),
    Mutation(
        "every flag appears in --help",
        "scholar_crawler/cli.py",
        'help="also export BibTeX entries to this .bib file; costs two extra requests "\n'
        '        "per record, so expect a slower run and more challenges",',
        "help=argparse.SUPPRESS,",
        DOCS,
    ),
    Mutation(
        "the usage line names only real flags",
        "scholar_crawler/cli.py",
        "(--recipes | --doctor | --self-check | --dry-run)",
        "(--recipes | --doctor | --self-check | --dry-run | --turbo)",
        DOCS,
    ),
    Mutation(
        "every recipe is a command that parses",
        "scholar_crawler/recipes.py",
        '-q "graph attention networks" -p 3 -o out/gat.jsonl',
        '-q "graph attention networks" -p 3 -o out/gat.jsonl --turbo',
        DOCS,
    ),
    Mutation(
        "the audit severities are the two documented words",
        "scholar_crawler/audit.py",
        '"warn",\n        "Scholar elided the venue',
        '"warning",\n        "Scholar elided the venue',
        OFFLINE,
    ),
    Mutation(
        "patents ride on as_sdt, citations on as_vis",
        "scholar_crawler/urls.py",
        'params["as_sdt"] = "0,5" if request.include_patents else "0"',
        'params["as_sdt"] = "0,5"',
        "tests/test_parser.py",
    ),
    Mutation(
        "the learned rhythm stays within its ceiling",
        "scholar_crawler/history.py",
        "    if history.back_to_back:\n        factor += 0.2",
        "    if history.back_to_back:\n        factor += 0.6",
        "tests/test_history.py",
    ),
    Mutation(
        "the takeover bell is a bell",
        "scholar_crawler/challenge.py",
        'sys.stderr.write("\\a")',
        "pass",
        "tests/test_challenge.py",
    ),
    Mutation(
        "state marks a target exhausted",
        "scholar_crawler/storage.py",
        '"exhausted": exhausted,',
        '"exhausted": False,',
        "tests/test_state.py tests/test_end_to_end.py",
    ),
    Mutation(
        "one list names every path a run writes",
        "scholar_crawler/storage.py",
        '    written.append(Written("browser profile", "--profile", profile, "dir"))',
        "    pass",
        "tests/test_explain.py tests/test_cli.py",
    ),
    Mutation(
        "the takeover log named is the one being written",
        "scholar_crawler/diagnose.py",
        '    named = log if log is not None else "the takeover log named by --challenge-log"',
        '    named = "out/challenges.jsonl"',
        "tests/test_diagnose.py",
    ),
    Mutation(
        "the doctor checks the paths this command writes",
        "scholar_crawler/cli.py",
        "            profile=args.profile, written=_written_paths(args), channel=args.channel or None",
        '            profile=args.profile, written=[], channel=args.channel or None',
        "tests/test_config.py",
    ),
    Mutation(
        "one directory is one finding",
        "scholar_crawler/doctor.py",
        "        if directory in seen:",
        "        if False:",
        "tests/test_doctor.py",
    ),
    Mutation(
        "a full disk keeps what it collected",
        "scholar_crawler/run.py",
        "    except OSError as error:",
        "    except _NeverRaised as error:",
        "tests/test_end_to_end.py",
    ),
    Mutation(
        "a dead pipe is not blamed on the disk",
        "scholar_crawler/diagnose.py",
        "    if error.filename is None and error.errno not in WRITE_ERRNOS:",
        "    if False:",
        "tests/test_diagnose.py",
    ),
    Mutation(
        "an output path is checked before anything is spent",
        "scholar_crawler/cli.py",
        "    blocked = _unwritable_output(args)",
        "    blocked = None",
        "tests/test_cli.py",
    ),
    Mutation(
        "a directory is not a file to write into",
        "scholar_crawler/storage.py",
        '    if kind == "file" and target.is_dir():',
        "    if False:",
        "tests/test_storage.py tests/test_cli.py",
    ),
    Mutation(
        "a list file that names nothing is a mistake",
        "scholar_crawler/cli.py",
        "    if not kept:",
        "    if False:",
        "tests/test_cli.py",
    ),
    Mutation(
        "a launch that fails is diagnosed, not raised",
        "scholar_crawler/browser.py",
        "        except PlaywrightError as error:",
        "        except _NeverRaised as error:",
        "tests/test_end_to_end.py",
    ),
    Mutation(
        "a profile that cannot be made is diagnosed too",
        "scholar_crawler/browser.py",
        '    unusable = unwritable(options.user_data_dir, kind="dir")',
        "    unusable = \"\"",
        "tests/test_end_to_end.py",
    ),
    Mutation(
        "an unforeseen failure still prints a document",
        "scholar_crawler/cli.py",
        "        if not args.json:\n            raise",
        "        if True:\n            raise",
        "tests/test_machine.py",
    ),
    Mutation(
        "a headless stop hands back an attended rerun",
        "scholar_crawler/recipes.py",
        '    if "--headless" not in given:\n        return None',
        '    if "--headless" in given:\n        return None',
        "tests/test_end_to_end.py",
    ),
    Mutation(
        "the refresh file names itself, not a placeholder",
        "scholar_crawler/refresh.py",
        '        f"# feed this back with: {refresh_command(path)}",',
        '        "# feed this back with: scholar-crawler --clusters-file <this file> -p 1",',
        "tests/test_refresh.py tests/test_real_pages.py",
    ),
    Mutation(
        "a finished target is not offered a way to continue",
        "scholar_crawler/modes.py",
        "            if request is not None and not entry.exhausted:",
        "            if request is not None:",
        "tests/test_state.py",
    ),
    Mutation(
        "the handed command keeps the filters of its target",
        "scholar_crawler/recipes.py",
        "        if request.review_only:\n            parts.append(\"--review-only\")",
        "        if False:\n            parts.append(\"--review-only\")",
        "tests/test_state.py tests/test_end_to_end.py",
    ),
    Mutation(
        "a stored target answers to the name it is shown under",
        "scholar_crawler/storage.py",
        "            if needle in entry.signature.casefold() or needle in entry.target.casefold()",
        "            if needle in entry.signature.casefold()",
        "tests/test_state.py",
    ),
    Mutation(
        "a signature holds a query containing the field separator",
        "scholar_crawler/models.py",
        "    parts = signature.rsplit(\"|\", SEARCH_FIELDS)",
        "    parts = signature.split(\"|\")",
        "tests/test_state.py",
    ),
    Mutation(
        "the bill quotes the rhythm the run will use",
        "scholar_crawler/plan.py",
        '        detail = f"{delay_span(self.pacing.min_delay, self.pacing.max_delay)} between requests"',
        '        detail = f"{self.pacing.min_delay:.0f}-{self.pacing.max_delay:.0f}s between requests"',
        "tests/test_history.py tests/test_end_to_end.py",
    ),
    Mutation(
        "the takeover log names the target, not the offset",
        "scholar_crawler/crawler.py",
        "            dump_tag=str(start),\n            target=request.describe(),",
        "            dump_tag=str(start),\n            target=str(start),",
        "tests/test_challenge_log.py tests/test_end_to_end.py",
    ),
    Mutation(
        "the browser clock agrees with the language it asks for",
        "scholar_crawler/cli.py",
        "        timezone=args.timezone or timezone_for(args.lang),",
        '        timezone=args.timezone or "America/Los_Angeles",',
        "tests/test_explain.py tests/test_end_to_end.py",
    ),
    Mutation(
        "a regional language tag is read whole",
        "scholar_crawler/browser.py",
        "    return TIMEZONES.get(regional) or TIMEZONES.get(base, DEFAULT_TIMEZONE)",
        "    return TIMEZONES.get(base, DEFAULT_TIMEZONE)",
        "tests/test_cli.py tests/test_end_to_end.py",
    ),
    Mutation(
        "a one-query collection titles its own report",
        "scholar_crawler/report.py",
        '        f"# {title if title is not None else title_for(records)}",',
        '        f"# {title or GENERIC_TITLE}",',
        "tests/test_report.py",
    ),
    Mutation(
        "a bare year is not a volume",
        "scholar_crawler/venues.py",
        '    r"\\s+(?P<volume>\\d+)"',
        '    r"\\s*(?P<volume>\\d+)"',
        "tests/test_venues.py tests/test_analysis.py",
    ),
    Mutation(
        "an elision does not become an author",
        "scholar_crawler/bibsynth.py",
        "    names = [_without_mark(name) for name in raw.split(\",\")]",
        '    names = [name.strip().strip("…").strip() for name in raw.split(",")]',
        "tests/test_real_pages.py",
    ),
    Mutation(
        "a journal name is not its volume and pages",
        "scholar_crawler/bibsynth.py",
        "    fields.extend((name, _escape(value)) for name, value in volume_fields(record))",
        "    fields.extend([])",
        "tests/test_bibsynth.py tests/test_real_pages.py",
    ),
    Mutation(
        "a cut venue name says so in the bibliography",
        "scholar_crawler/bibsynth.py",
        '    return f"{name} ..." if parsed.cut else name',
        "    return name",
        "tests/test_bibsynth.py",
    ),
    Mutation(
        "a first citation is a movement, not silence",
        "scholar_crawler/collection.py",
        "        if now is None or was == now:",
        "        if was is None or now is None or was == now:",
        "tests/test_collection.py tests/test_real_pages.py",
    ),
    Mutation(
        "one work with a new id is not two works",
        "scholar_crawler/collection.py",
        "        reclustered=sorted(set(added) & set(gone)),",
        "        reclustered=[],",
        "tests/test_collection.py tests/test_real_pages.py",
    ),
    Mutation(
        "one crawl does not get reported as a span of ages",
        "scholar_crawler/refresh.py",
        '        if max(ages) - min(ages) < 1',
        "        if False",
        "tests/test_refresh.py tests/test_real_pages.py",
    ),
    Mutation(
        "the refresh file stays the format the crawler reads",
        "scholar_crawler/refresh.py",
        '        lines.append(cluster_id)',
        '        lines.append(f"# {cluster_id}")',
        "tests/test_real_pages.py tests/test_refresh.py",
    ),
    Mutation(
        "a venue Scholar cut keeps its mark",
        "scholar_crawler/analysis.py",
        '    return " ".join(head + [parsed.name] + tail)',
        "    return parsed.name",
        "tests/test_real_pages.py tests/test_analysis.py",
    ),
    Mutation(
        "a profile row is not audited for a card id it never has",
        "scholar_crawler/audit.py",
        '    if (record.get("extra") or {}).get("citation_id"):',
        "    if False:",
        "tests/test_real_pages.py",
    ),
    Mutation(
        "a pure mode prints exactly what the docs show",
        "scholar_crawler/plan.py",
        'lines.append(f"total: up to {self.total_loads} page loads for {self.total_records} records")',
        'lines.append(f"total: {self.total_loads} page loads for {self.total_records} records")',
        "tests/test_documented_commands.py",
    ),
    Mutation(
        "a renamed report label leaves the docs lying",
        "scholar_crawler/analysis.py",
        'f"citation-only    {summary.citation_only}",',
        'f"citations only   {summary.citation_only}",',
        "tests/test_documented_commands.py",
    ),
    Mutation(
        "a documented output channel still exists",
        "scholar_crawler/run.py",
        'print(f"[audit] {line}", flush=True)',
        'print(f"[check] {line}", flush=True)',
        "tests/test_documented_commands.py",
    ),
    Mutation(
        "the skipped-file list names each file once",
        "scholar_crawler/digest.py",
        "    written = list(dict.fromkeys(path for path in (args.out, args.since) if path is not None))",
        "    written = [path for path in (args.out, args.since) if path is not None]",
        "tests/test_collection.py",
    ),
    Mutation(
        "a documented URL is the URL the tool builds",
        "README.md",
        "q=graph+attention+networks&as_vis=0",
        "q=graph+attention&as_vis=0",
        "tests/test_documented_commands.py",
    ),
    Mutation(
        "a documented command still runs",
        "README.md",
        "scholar-digest out/all.jsonl --min-citations 1000",
        "scholar-digest out/all.jsonl --min-cites 1000",
        "tests/test_documented_commands.py",
    ),
    Mutation(
        "a note is not listed among the problems to fix",
        "scholar_crawler/doctor.py",
        '        heading = "also worth knowing, but nothing to fix:"',
        '        heading = f"{counted} must be fixed before a crawl can run:"',
        "tests/test_doctor.py",
    ),
    Mutation(
        "one mode reads the command back and costs it",
        "scholar_crawler/cli.py",
        "        for line in explain(",
        "        for line in () and explain(",
        "tests/test_explain.py",
    ),
    Mutation(
        "the profile file is named after the records file",
        "scholar_crawler/storage.py",
        'return records.with_suffix(".profiles.jsonl")',
        'return Path("out/profiles.jsonl")',
        "tests/test_storage.py",
    ),
    Mutation(
        "the browser speaks the interface language",
        "scholar_crawler/browser.py",
        'return "en-US" if language == "en" else language',
        'return "en-US"',
        "tests/test_end_to_end.py",
    ),
    Mutation(
        "an unattended drill is not read as a block",
        "scholar_crawler/storage.py",
        'return self.outcome == "rehearsed" or self.target == REHEARSAL_TARGET',
        'return self.outcome == "rehearsed"',
        "tests/test_history.py",
    ),
    Mutation(
        "a takeover names its evidence file",
        "scholar_crawler/cli.py",
        '        if ran.outcome.stats.handoffs:',
        '        if False:',
        "tests/test_end_to_end.py",
    ),
    Mutation(
        "a batch tells each target its place",
        "scholar_crawler/run.py",
        'place = (lambda index: f"{index}/{seeds}") if seeds > 1 else (lambda _index: "")',
        'place = lambda _index: ""  # noqa: E731',
        "tests/test_end_to_end.py",
    ),
    Mutation(
        "a port the browser blocks is named as a refusal",
        "scholar_crawler/diagnose.py",
        '    ("ERR_UNSAFE_PORT", Failure.CONNECTION_REFUSED),',
        "",
        "tests/test_diagnose.py",
    ),
    Mutation(
        "markdown punctuation in a title is escaped",
        "scholar_crawler/report.py",
        'MARKDOWN_SPECIALS = "\\\\`*_[]<>|"',
        'MARKDOWN_SPECIALS = "|"',
        "tests/test_report.py",
    ),
    Mutation(
        "a link destination survives a bare parenthesis",
        "scholar_crawler/report.py",
        'return f"[{title}](<{link}>)" if isinstance(link, str) and link else title',
        'return f"[{title}]({link})" if isinstance(link, str) and link else title',
        "tests/test_report.py",
    ),
    Mutation(
        "the silent latex specials are escaped",
        "scholar_crawler/bibsynth.py",
        '    "^": r"\\textasciicircum{}",',
        '    "^": "^",',
        "tests/test_bibsynth.py",
    ),
    Mutation(
        "a bad argument vector still answers with a document",
        "scholar_crawler/machine.py",
        "    if not as_json:",
        "    if as_json or not as_json:",
        "tests/test_machine.py",
    ),
    Mutation(
        "a refusal carries its reason into the document",
        "scholar_crawler/cli.py",
        '            ran.reason or "the command did not describe a run",',
        '            "the command did not describe a run",',
        "tests/test_machine.py",
    ),
    Mutation(
        "a stale editable install is reported",
        "scholar_crawler/doctor.py",
        "    if source is None or installed == source:",
        "    if True or source is None or installed == source:",
        "tests/test_doctor.py",
    ),
    Mutation(
        "a rerun is told what it would redo",
        "scholar_crawler/run.py",
        "        if (offset := state.next_start(signature)) > 0",
        "        if (offset := state.next_start(signature)) > 10_000",
        "tests/test_state.py",
    ),
    Mutation(
        "--doctor judges the browser the run would launch",
        "scholar_crawler/doctor.py",
        "    if found := _channel_path(channel):",
        "    if False and (found := _channel_path(channel)):",
        "tests/test_doctor.py tests/test_recipes.py",
    ),
    Mutation(
        "a missing channel stops the run rather than warning",
        "scholar_crawler/doctor.py",
        '            f"{channel} was not found in the usual places, though {bundled} is ready",',
        '            f"{channel} is everywhere, though {bundled} is ready",',
        "tests/test_doctor.py",
    ),
    Mutation(
        "an alarm needs a share as well as a count",
        "scholar_crawler/audit.py",
        "ALARM_SHARE = 0.2",
        "ALARM_SHARE = 0.0",
        "tests/test_audit.py",
    ),
)


def check_table() -> list[str]:
    """Verify every mutation can be applied exactly as written.

    :returns: one complaint per entry that cannot be applied, empty when the table is sound.
    """
    complaints = []
    for mutation in MUTATIONS:
        path = ROOT / mutation.path
        if not path.exists():
            complaints.append(f"{mutation.label}: {mutation.path} does not exist")
            continue
        found = path.read_text(encoding="utf-8").count(mutation.original)
        if found != 1:
            complaints.append(
                f"{mutation.label}: {found} occurrences of the text in {mutation.path}; "
                "a mutation must name exactly one place"
            )
    return complaints


def _write(path: Path, text: str) -> None:
    """Replace a source file and drop the bytecode compiled from the old text.

    Python reuses a ``.pyc`` whose recorded source mtime and size still match, and a mutation
    that swaps one character for another keeps the size while the restore lands in the same
    second. Without this the next run reads stale bytecode: an unguarded invariant that is
    guarded, or the reverse.

    :param path: the source file to overwrite.
    :param text: its new content.
    """
    path.write_text(text, encoding="utf-8")
    shutil.rmtree(path.parent / "__pycache__", ignore_errors=True)


def _run(tests: str) -> tuple[bool, str]:
    """Run pytest over the given selectors.

    :param tests: space-separated pytest files or node ids.
    :returns: whether everything passed, and the summary line.
    """
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests.split()],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    lines = done.stdout.strip().splitlines()
    return done.returncode == 0, lines[-1] if lines else "no output"


def audit(mutations: tuple[Mutation, ...]) -> list[Mutation]:
    """Apply each mutation, run its tests, and restore the file.

    :param mutations: the entries to check.
    :returns: the mutations no test noticed.
    """
    unguarded = []
    for mutation in mutations:
        path = ROOT / mutation.path
        source = path.read_text(encoding="utf-8")
        broken = source.replace(mutation.original, mutation.broken, 1)
        if broken == source:
            raise ValueError(
                f"{mutation.label}: the text is not in {mutation.path}, so nothing was broken; "
                "an unapplied mutation reads as an unguarded invariant"
            )
        _write(path, broken)
        try:
            passed, summary = _run(mutation.tests)
        finally:
            _write(path, source)
        if passed:
            unguarded.append(mutation)
        verdict = "UNGUARDED" if passed else "caught"
        print(f"{verdict:<10} {mutation.label}  [{summary}]", flush=True)
    return unguarded


def main(argv: list[str]) -> int:
    """Run the audit from the command line.

    :param argv: arguments after the module name; ``--all`` includes slow entries, any other
        argument filters labels by substring.
    :returns: process exit code.
    """
    include_slow = "--all" in argv
    patterns = [arg for arg in argv if not arg.startswith("--")]
    selected = tuple(
        mutation
        for mutation in MUTATIONS
        if (include_slow or not mutation.slow)
        and (not patterns or any(pattern in mutation.label for pattern in patterns))
    )
    complaints = check_table()
    if complaints:
        print("the mutation table no longer fits the source:")
        for complaint in complaints:
            print(f"  {complaint}")
        return 2
    if not selected:
        print("no mutation matched")
        return 2
    skipped = len(MUTATIONS) - len(selected)
    print(f"{len(selected)} mutations" + (f", {skipped} skipped" if skipped else ""), flush=True)
    unguarded = audit(selected)
    if unguarded:
        print(f"\n{len(unguarded)} invariant(s) no test protects:")
        for mutation in unguarded:
            print(f"  {mutation.label} ({mutation.path})")
        return 1
    print("\nevery mutation was caught")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
