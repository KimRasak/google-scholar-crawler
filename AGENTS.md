# AGENTS.md — using this tool from a program

This file is for an AI agent (or any script) that collects papers with this tool. Humans want
[README.md](README.md); it is long because it explains why. This one is short because a caller
only needs the interface.

## Install

```sh
pip install git+https://github.com/KimRasak/google-scholar-crawler   # pipx install works too
scholar-crawler --doctor                                             # says what is missing
scholar-crawler --install-browser                                    # only if --doctor asks
```

Runs drive the browser named by `--channel`, `chrome` by default, so a machine with Chrome
installed needs no download. `--install-browser` runs Playwright's download inside the
environment the tool landed in, so it works under `pipx`, a venv, or a plain `pip install`.
`--doctor` exits 1 only when the browser this run would launch is absent.

## The one rule you cannot code around

**Google's verification pages are handed to a human, never solved.** When Scholar shows a
CAPTCHA, the browser window is brought to the front and the run waits. So:

- Run without `--headless` when a person can reach the screen.
- With `--headless`, a challenge ends the run with `"error": {"kind": "challenge_unattended"}`.
  That is not a bug to retry harder — surface it to a human, let them clear it once, and the
  persistent profile (`--profile`, default `.scholar-profile`) reuses the cleared cookies.
- Never add a solver, a proxy rotation, or a retry loop against a challenge. Scraping Scholar is
  against its terms; for bulk metadata use Semantic Scholar, OpenAlex or Crossref instead.

## Collect papers

```sh
scholar-crawler -q "graph attention networks" -p 2 --json          # 2 pages, 10 records each
scholar-crawler -q "..." -p 5 --dry-run --json                     # read it back and cost it
scholar-crawler --cites 2960712678066186980 -p 2 --json            # who cites this paper
scholar-crawler --author kukA0LcAAAAJ -n 200 --json                # one author's publications
```

`--json` prints exactly one JSON object on stdout and sends every progress line to stderr, so
`json.loads(stdout)` always works. Pass it with a real crawl or with `--dry-run`; it is refused
together with the report modes (`--doctor`, `--recipes`, `--self-check`) because those print
reports rather than results.

```json
{
  "tool": "scholar-crawler",
  "version": "0.2.0",
  "ok": true,
  "exit_code": 0,
  "counts": { "records": 20, "duplicates": 0, "requests": 2, "takeovers": 0 },
  "files": { "records": "out/results.jsonl", "state": "out/state.json" },
  "records": [ { "title": "...", "cluster_id": "...", "cited_by_count": 1234, "...": "..." } ],
  "error": null
}
```

With `--dry-run` the document adds `"plan": {"page_loads", "records_at_most", "seconds",
"cooldowns", "targets"}` and collects nothing. `files` names only the files the run actually
wrote; `counts.takeovers > 0` adds `challenges`, the JSONL log of what blocked the run and how
each takeover ended.

One record carries exactly these keys: `cluster_id`, `position`, `title`, `link`,
`resource_link`, `resource_type`, `byline`, `authors`, `venue`, `year`, `snippet`,
`cited_by_count`, `cited_by_url`, `versions_count`, `versions_url`, `related_url`,
`citation_only`, `query`, `page_start`, `fetched_at`, `extra`. Absent fields are `null`; counts
come from Scholar's own links, so they are language-independent. `cluster_id` is the identity
used for deduplication, and feeding it back as `--cites` or `--cluster` walks the citation graph.

## Digest what you collected (no requests)

```sh
scholar-digest --collection out --json                       # merge, dedup, overview
scholar-digest --collection out --since out/merged.jsonl -o out/merged.jsonl --json
scholar-digest --collection out --report out/report.md --bibtex out/refs.bib
```

The digest document adds `"overview"` with exactly `records`, `citations`, `with_bibtex`,
`citation_only`, `unknown_year`, `years`, `venues`, `most_cited`; and with `--since` a `"delta"`
with exactly `before`, `after`, `added`, `gone`, `unchanged`, `reclustered`, `citations_gained`,
`moved` — the answer to "what changed since last time" without re-crawling. `reclustered` names
the titles listed as both added and gone: one work Scholar gave a new id, not two works. A count
arriving where there was none is a movement from 0, because Scholar shows no citing-works link
until a work has one. Its `"counts"` are `records`, `read`, `files`, `duplicates`,
`filtered_out`, `unreadable_lines`.

## Exit codes and errors

`0` success · `1` usage error or a stopped run · `130` interrupted.

On failure the document carries `"error": {"kind", "message", "next_steps"}`. Branch on `kind`:

| `kind` | What to do |
| --- | --- |
| `challenge_unattended` | ask a human to run it once without `--headless` |
| `rate_limited` | stop for a while, then resume with `--resume` and slower `--min-delay` |
| `connection_refused`, `dns`, `offline`, `proxy`, `certificate` | a local network problem, not Scholar |
| `unknown_layout` | run `scholar-crawler --self-check`; Scholar's HTML may have changed |
| `timeout`, `reset` | transient; the run already retried, so retry later at a slower pace |
| `http_error` | Scholar answered with an error status; the message carries it |
| `browser_closed` | the window was closed mid-run; rerun with `--resume` |
| `usage`, `bad_inputs`, `no_records`, `missing_since`, `unreadable_input` | fix the command; `message` carries the reason |
| `unsupported_mode` | that flag prints a report, so run it without `--json` |
| `interrupted` | Ctrl+C; whatever was collected is already on disk |
| `runtime_error`, `unknown` | unexpected; read `message`, and `--dump-html` keeps the page that caused it |

Every value above is the complete vocabulary — a run never invents a `kind` outside this list.

## Budget before you spend

One page load ≈ 10 records, with 4–11 s between loads by default and a 90 s cooldown every 10
loads. `--bibtex` costs two extra loads per record. `--dry-run --json` gives the exact estimate.
Being slow is the feature: it is what keeps a run from being challenged.

Runs are resumable: every page is appended immediately, and `--resume` continues each target
from the offset in `out/state.json`. A stopped run has already kept what it collected.
