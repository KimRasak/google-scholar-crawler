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
        "return self.cluster_id or f\"{self.title}::{self.link or ''}\"",
        "return f\"{self.title}::{self.link or ''}\"",
        OFFLINE,
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
        '"usage"',
        '"usage_x"',
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
        "(--recipes | --doctor | --self-check | --dry-run | --explain)",
        "(--recipes | --doctor | --self-check | --dry-run | --explain | --turbo)",
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
