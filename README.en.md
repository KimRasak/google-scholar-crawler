# Google Scholar crawler with human takeover

[![tests](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml/badge.svg)](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml)
English | [中文](README.md)

Runs academic searches on Google Scholar in a real browser (Playwright + your installed Chrome). Pagination, parsing and output are automated; the moment Google shows a reCAPTCHA, a `/sorry/` interstitial, a consent wall or a sign-in prompt, the crawler stops and hands the visible window to you, then resumes on its own once the page is a normal result page again.

The code never tries to solve, bypass or hide a verification challenge. Verification is done by a human, in a window they can see.

## Three steps

**1. Install** (two commands; details under [Install](#install))

```sh
pip install git+https://github.com/KimRasak/google-scholar-crawler   # pipx install works too
scholar-crawler --install-browser                                    # only if Chrome is missing
```

**2. Collect once** (one request, about five seconds, in a real Chrome window)

```sh
$ scholar-crawler -q "retrieval augmented generation survey" -p 1 -o out/rag.jsonl
[query] 'retrieval augmented generation survey' from offset 0
[page] offset=0 parsed=10 new=10 total=~548000
[out] 10 new records (0 duplicates skipped) -> out/rag.jsonl
[run] 1 request in 5s, 0 takeovers, 0 navigation retries, delay now 4.0-11.0s
```

**3. See what you got**

One record per line in `out/rag.jsonl`, carrying everything a Scholar result card shows (full field list under [Output](#output)):

```json
{"title":"Retrieval-augmented generation for large language models: A survey",
 "authors":"Y Gao, Y Xiong, X Gao, K Jia, J Pan","year":2023,"venue":"arXiv preprint",
 "cited_by_count":7878,"cited_by_url":"https://scholar.google.com/scholar?cites=...",
 "cluster_id":"...","link":"...","query":"retrieval augmented generation survey"}
```

`out/state.json` appears alongside it — the resume cursor, so `--resume` continues from here next time. Forgetting it costs nothing: running the same target again opens with `[state] ... already reached offset 10`. To see what a collection looks like, digest it without sending anything:

```sh
scholar-digest out/rag.jsonl                # size, years, venues, the most cited
scholar-digest out/rag.jsonl --report out/report.md   # the same as readable Markdown
```

From here, pick a section from [Where to start](#where-to-start), or run `scholar-crawler --recipes` and copy a command. Calling it from an agent is one page: [AGENTS.md](AGENTS.md).

## What a block looks like

Google showing a verification page mid-crawl is normal, and this whole tool is built around that moment: **it does not recognize, bypass or hide a challenge** — it hands the window to a person. A real wait reads like this (with the URL replaced by a `/sorry/` one):

```
[handoff] captcha: matched #gs_captcha_ccl
[handoff] URL: https://www.google.com/sorry/index?continue=...
[handoff] The browser window is yours. Solve the challenge (or accept the
[handoff] consent/sign-in page) and leave it on the Scholar result page.
[handoff] No keypress needed — the page is re-checked every 2s and crawling resumes by itself. You have 600s to act.
[handoff] Press Ctrl+C to stop instead.
[handoff] waiting 15s so far, 585s left; still showing captcha
[handoff] the page is now a sign_in: account sign-in wall
[handoff] still waiting; 60s left before the run gives up and stops with whatever it collected
[handoff] cleared after 128s — resuming automated crawl.
[pace] backing off to 6.4-17.6s between pages
```

You have exactly one job: clear the challenge in the window that just came to the front, then press nothing. The details exist because the person it waits for has usually stepped away:

- **No keypress is needed.** The page is re-inspected every two seconds and the crawl resumes by itself; the opening message says how long there is (`--handoff-timeout 0` waits forever).
- **A change of challenge is announced.** Clearing a captcha only to land on a sign-in wall asks something different of you, so the wait says `the page is now a sign_in`.
- **It rings again before giving up**, 60 seconds ahead, saying the run will stop with whatever it collected.
- **Being challenged makes it slower.** After a takeover, page delays are multiplied by `--backoff-factor` (×1.6 by default), and the next run starts slower by reading [the takeover log](#the-takeover-log).
- **Nothing is lost.** Records are flushed page by page, and under `--headless`, where nobody can take over, the run stops as `challenge_unattended` with everything collected so far still on disk.

Expect one takeover on the very first run, because the profile has no cookies yet; once a human clears it, the cookies live in `.scholar-profile` and are reused. See also [Getting challenged less](#getting-challenged-less), [Rehearsing the human takeover](#rehearsing-the-human-takeover) and [Learning to slow down across runs](#learning-to-slow-down-across-runs).

## Where to start

| Your situation | Sections to read |
| --- | --- |
| First time | [Three steps](#three-steps) → [What a block looks like](#what-a-block-looks-like) |
| Collecting a batch | [More commands](#more-commands) → [Reading the command back and costing it](#reading-the-command-back-and-costing-it---dry-run) → [Options](#options) |
| Using what you collected | [Digesting collected results](#digesting-collected-results-no-requests) → [A readable overview](#a-readable-overview---report) → [Building a bibliography offline](#building-a-bibliography-offline) |
| Blocked too often | [What a block looks like](#what-a-block-looks-like) → [Getting challenged less](#getting-challenged-less) → [The takeover log](#the-takeover-log) |
| It stopped, or parsing looks wrong | [Failures in plain words](#failures-in-plain-words) → [Self-check](#self-check) → `--dump-html` |
| Interrupted run | [Reviewing and resetting resume state](#reviewing-and-resetting-resume-state) → `--resume` |
| Driving it from an agent | [Calling it from a program](#calling-it-from-a-program---json) → [AGENTS.md](AGENTS.md) (the whole interface on one page) |
| Changing the code | [Development](#development) → [Layout](#layout) → [How it works](#how-it-works) |

The short version: `scholar-crawler --recipes` hands you working commands; come back to the matching section when something goes wrong.

## How it works

1. Launches headed Chrome with a **persistent profile** (`--profile`, default `.scholar-profile`).
2. Waits a random 4–11 seconds before every request, with a 90-second cooldown every 10 requests. The counter spans the whole run — across queries, across authors, including BibTeX loads — instead of resetting per query. A scroll and short dwell after load keep the rhythm off a machine grid.
3. Classifies every page after navigation: a challenge hands the window to a person ([what a block looks like](#what-a-block-looks-like)), anything else is parsed as a result page.
4. Appends results to JSONL and flushes per page, recording the next unfetched offset per query in a state file, which is where `--resume` continues from.

Detection uses the URL (`/sorry/`, `consent.google.`, `accounts.google.com`), DOM selectors (`#gs_captcha_ccl`, `#gs_captcha_f`, `form#captcha-form`, reCAPTCHA iframes) and body text when no results are present. Citation and version counts are read from link hrefs, so parsing is independent of the interface language.

## Install

`pip install git+https://github.com/KimRasak/google-scholar-crawler` then `scholar-crawler --install-browser` is the whole install (see [Three steps](#three-steps)). The first line uses `pip` because `pip` is always there: `pipx install <the same URL>` puts the tool in its own environment and is the tidier choice, but pipx itself has to be installed first, which makes it the alternative rather than the first step. The second command runs Playwright's download through the current interpreter, so the browser lands in the right place whether the tool sits in a pipx environment, a venv, or the system Python. It is the one step a new user cannot guess, which is why it is a command rather than a paragraph.

On a machine that already has Chrome you can skip it: the default `--channel chrome` drives the system browser, and the 550 MB Chromium is never downloaded or opened. `--doctor` checks only the browser this run would launch, so it passes on such a machine.

To change the code, install from a checkout:

```sh
git clone https://github.com/KimRasak/google-scholar-crawler.git
cd google-scholar-crawler
python3 -m pip install -e .              # provides the scholar-crawler command
scholar-crawler --install-browser
```

Python 3.10+. Keep the default `--channel chrome` when Chrome is installed. `scholar-crawler ...` and `python3 -m scholar_crawler ...` are equivalent, and `scholar-crawler --version` reports which version is installed.

Check the machine first; this sends no request:

```sh
$ scholar-crawler --doctor
[doctor] + python                 3.13.5 at /opt/miniconda3/bin/python3
[doctor] + version                0.2.0
[doctor] + playwright             1.60.0
[doctor] + bs4                    4.14.3
[doctor] + lxml                   6.1.0
[doctor] + settings files         tomllib (stdlib) reads --config files
[doctor] + browser                chrome at /Applications/Google Chrome.app/...; bundled Chromium is also available
[doctor] ! profile                .scholar-profile holds no cookies yet, so the first challenge will need a human
[doctor] + output                 out is writable
[doctor] nothing is broken; these are worth knowing:
[doctor]   profile: expect one takeover on the first run; the cleared cookies are then reused
```

A machine that is not ready yet looks like this, which is the screen a new install is most likely to see (`--channel ''` means this run wants the bundled Chromium, and it has not been downloaded):

```sh
$ scholar-crawler --doctor --channel ''
[doctor] x browser                bundled Chromium not downloaded (expected at .../chromium-1234/...)
[doctor] ! profile                .scholar-profile holds no cookies yet, so the first challenge will need a human
[doctor] 1 problem must be fixed before a crawl can run:
[doctor]   browser: scholar-crawler --install-browser
[doctor] also worth knowing, but nothing to fix:
[doctor]   profile: expect one takeover on the first run; the cleared cookies are then reused
```

What must be fixed and what is merely worth knowing are separate blocks: a `!` printed under "must be fixed" reads as a second problem, and the first screen a new install sees should not make itself sound worse than it is. Running the command it names (`--install-browser`, 280 MB down and 550 MB on disk) turns the same `--doctor` into exit 0.

It checks the Python version against 3.10, that the installed version still matches the sources being run, that all three dependencies import and are no older than the floors declared in `pyproject.toml`, that TOML settings files can be read (stdlib `tomllib` from 3.11, `tomli` on 3.10), that **the browser this run would actually launch** is there, whether the profile already holds cookies from earlier runs, and whether the output and state directories can be written. Every failure names the command that fixes it (`pip install -e .`, `scholar-crawler --install-browser`, `--channel ''`, …), and any `x` exits 1.

Three deliberate choices: the browser is one check because a run launches one browser — with the default `--channel chrome`, a system Chrome is enough and an undownloaded Chromium costs nothing, while a channel that is not installed is a failure because that exact command cannot start; a version mismatch is only a warning but names the reinstall, because otherwise the `version` in every `--json` document stays the one pip recorded at install time; and the check creates no directories — a mistyped path should not leave empty shells on disk, so it probes the closest existing ancestor and says plainly that the directory does not exist yet while its parent is writable.

Once the machine is sound, `--self-check` goes on to test the network.

## More commands

Rather than reading the flag table, start from `--recipes`: seventeen complete commands to copy. **The first one collects a topic** — a list opening with three diagnostics would answer a question nobody asked. The checks come after it, and the rest run roughly from cheapest to most expensive. A run given nothing to do prints the first three after the error, so copying a line out of the error message starts a crawl.

```sh
$ scholar-crawler --recipes
1. Collect one topic — start here
   $ scholar-crawler -q "graph attention networks" -p 3 -o out/gat.jsonl
     3 pages, 10 records each, about a minute; clear any challenge in the window it opens
2. Check that this machine can run a crawl at all
   $ scholar-crawler --doctor
     no requests; reports Python, the libraries, the browser and the directories
...
```

The tests keep these honest: every recipe is parsed by the real parser and must build a crawl target, and the `--dry-run` one is actually executed. A renamed flag or a typo fails the suite instead of handing you a command that does not run.

```sh
# Keyword search, three pages (10 results each)
scholar-crawler -q "large language model agents" -p 3 -o out/agents.jsonl

# Year filter, sorted by date, capped at 40 records
scholar-crawler -q "retrieval augmented generation" \
  --year-from 2023 --sort-by-date -n 40 -o out/rag.jsonl

# Batch queries (one per line, # comments allowed) with resume
# with several targets each one reports its place: [query] 3/12 '...' from offset 0
scholar-crawler --queries-file queries.example.txt -p 10 --resume -o out/batch.jsonl

# Follow the citation graph: paste cited_by_url / versions_url from collected records
scholar-crawler --cites "https://scholar.google.com/scholar?cites=2454404157773228931" -p 5 -o out/citing.jsonl
scholar-crawler --cluster 2454404157773228931 -o out/versions.jsonl

# Walk one level out along the citation graph
scholar-crawler -q "chain of thought prompting" -p 1 \
  --follow-cites 1 --follow-breadth 3 --follow-min-citations 50 -o out/graph.jsonl

# Export BibTeX along the way (two extra page loads per record)
scholar-crawler -q "diffusion models" -p 2 --bibtex out/refs.bib -o out/diffusion.jsonl

# Crawl an author profile: up to 100 publications per request, header stored separately
scholar-crawler --author kukA0LcAAAAJ -o out/bengio.jsonl
scholar-crawler --author "https://scholar.google.com/citations?user=kukA0LcAAAAJ&hl=en" --sort-by-date -p 2

# Scholar's advanced syntax goes straight into the query
scholar-crawler -q 'author:"Yoshua Bengio" source:"NeurIPS"' -p 2
```

## Calling it from a program: `--json`

This tool is increasingly run by agents, which do not want progress lines — they want one call and one parsable result. `--json` gives stdout to a single JSON object and sends every human line to stderr, so `json.loads(stdout)` always works:

```sh
$ scholar-crawler -q "graph attention networks" -p 1 --json 2>/dev/null
{
  "tool": "scholar-crawler",
  "version": "0.2.0",
  "ok": true,
  "exit_code": 0,
  "counts": { "records": 10, "duplicates": 0, "requests": 1, "takeovers": 0 },
  "files": { "records": "out/results.jsonl", "state": "out/state.json" },
  "records": [ { "title": "...", "cluster_id": "...", "cited_by_count": 1234, "...": "..." } ],
  "error": null
}
```

The eight top-level keys stay put: `tool`, `version`, `ok`, `exit_code`, `counts`, `files`, `records`, `error`. The conventions around them:

- **`records` carries the records themselves**, so a caller never reads the file back. The files are still written, and `files` names them.
- **`--dry-run --json` costs a run without sending anything** and adds `plan` (`page_loads`, `records_at_most`, `seconds`, `cooldowns`, `targets`) — the way an agent decides whether a search is worth its requests.
- **A failure is also a document**: `error` is `{kind, message, next_steps}`, and `kind` comes from a closed vocabulary (`challenge_unattended`, `rate_limited`, `unknown_layout`, `connection_refused`, …) that a caller can switch on. The vocabulary is enforced: writing a `kind` outside it raises rather than reaching a caller.
- **Report modes and `--json` are mutually exclusive.** `--doctor`, `--recipes` and `--self-check` *are* reports for people; pairing them with `--json` would promise a result that does not exist, so it is refused — and the refusal is itself an `unsupported_mode` document.

`scholar-digest --json` works the same way and adds `overview` (records, citations, years, venues, most cited) plus, with `--since`, `delta` (added, gone, moved, citations gained): what changed since last time, without re-crawling.

The rule that matters most to an agent is that **a challenge belongs to a human**: under `--headless` a challenge ends the run with `challenge_unattended`, and the correct response is not to retry harder but to hand it to a person once. The whole calling interface is one page: [AGENTS.md](AGENTS.md), written for programs, while this README is written for people.

## Digesting collected results (no requests)

Crawling in short sessions leaves results spread over several JSONL files. `scholar-digest` reads local files only, and merges, filters, summarizes and exports them:

```sh
# merge several result files into one deduplicated set, plus a CSV
scholar-digest out/*.jsonl -o out/all.jsonl --csv out/all.csv

# keep only well-cited recent work
scholar-digest out/all.jsonl --min-citations 1000 --year-from 2018 -o out/hot.jsonl
```

Given no file to write, it prints an overview and stops:

```sh
$ scholar-digest out/all.jsonl
[in] 38 records from 1 file(s), 3 duplicates merged, 0 filtered out
  records          38
  citations        104392 total
  bibtex keys      7
  citation-only    2
  unknown year     1
  years            2024:3, 2023:9, 2022:7, 2021:6, 2020:5, 2019:4, 2018:3
  graph levels     L0:20, L1:18
  venues              6  Advances in neural information processing systems
                      4  ICLR
                      3  arXiv preprint
  most cited        41135  2018  Graph attention networks
                     8204  2019  Heterogeneous graph attention network
                     3205  2019  Kgat: Knowledge graph attention network for recommendation
```

`graph levels` appears only when the collection really holds records pulled in by `--follow-cites` (`L0` is what the search returned, `L1` their citing works), and `citation-only` counts the records Scholar lists with a citation but no page of their own.

When the same work appears in several files, the higher citation count wins as the fresher observation, the fuller record wins ties, `extra.bibtex_key` survives, and `follow_depth` keeps the shallowest level.

`--help` is ordered as "which records → read in the terminal → write to a file", and each group says when to use it.

**Inputs**

| Option | Effect |
| --- | --- |
| `FILE ...` | JSONL files to read, named directly |
| `--collection DIR` | treat a folder as one collection: read every `.jsonl` in it, excluding the files this run writes |
| `--since FILE` | compare against an earlier merge: what arrived, what is no longer here, which counts moved |

**Selection** — everything below covers this set

| Option | Effect |
| --- | --- |
| `--min-citations`, `--year-from`, `--year-to` | filters; a year range drops records without a year |

**Printed reports** — these write nothing

| Option | Effect |
| --- | --- |
| `--top` | entries in every printed list: most cited, stale, cited from inside (default: 5; `0` keeps the counts and drops the lists) |
| `--group-by`, `--groups` | group by `author`, `venue`, `year` or `level`; how many groups to list (default: 10) |
| `--audit` | audit fields: implausible values and missing rates, as errors and warnings |
| `--network` | report the citation graph the records already carry |
| `--stale [DAYS]` | report how old the collection is, most-moved records first |

**Written outputs** — hand the collection to something else

| Option | Effect |
| --- | --- |
| `-o`, `--csv` | write the merged records as JSONL / CSV (the only CSV export) |
| `--bibtex` | build a bibliography offline (no requests) |
| `--report`, `--report-title`, `--report-top` | write a readable Markdown overview; how many records it lists (default: 15) |
| `--refresh-list`, `--refresh-limit` | write the cluster ids worth re-listing |
| `--quiet` | print only what was written; needs one of the writing options |

`--top` governs terminal lists only and `--report-top` the Markdown report, so shortening what you read does not shorten what you wrote.

### Building a bibliography offline

Exporting BibTeX during a crawl costs two extra page loads per record, the most expensive part of a run. But the title, authors, venue, year and link from the result card are already stored, so a usable bibliography can be assembled afterwards without contacting Scholar:

```sh
scholar-digest out/all.jsonl --min-citations 500 --bibtex out/refs.bib
[out] 42 entries -> out/refs.bib (7 keys from the crawl, 35 generated, 12 truncated author lists)
```

These are reconstructions, not Scholar's own export, and the difference is worth knowing:

```bibtex
% exported by Scholar (--bibtex during the crawl, 2 requests per record)
@article{velivckovic2017graph,
  title={Graph attention networks},
  author={Veli{\v{c}}kovi{\'c}, Petar and Cucurull, Guillem and Casanova, Arantxa and ...},
  journal={arXiv preprint arXiv:1710.10903},
  year={2017}
}

% assembled offline (0 requests)
@article{velickovic2017graph,
  title = {{Graph attention networks}},
  author = {P Veličković and G Cucurull and A Casanova and others},
  journal = {arXiv preprint},
  year = {2017},
  url = {https://arxiv.org/abs/1710.10903},
  note = {cited by 41135 on Google Scholar},
}
```

So: author names keep Scholar's initials, a list Scholar truncated gains `and others`, and the arXiv identifier may be missing — in exchange for zero requests, plus the original link and the citation count. Export during the crawl when you need exact entries; build offline when you want a usable bibliography without waiting hours again.

Details: a record exported during the crawl reuses its key, so both files name the same work identically; `Veličković` transliterates to `velickovic` (`ł`, `ø`, `ß`, `æ` are handled too); colliding keys gain `a`, `b`, …; a venue mentioning Proceedings, Conference or Workshop becomes `@inproceedings` with `booktitle`, and a record without a venue becomes `@misc`; titles are double-braced so a style cannot flatten their capitalization; `&`, `%`, `$`, `#` and `_` are escaped, and so are `^` and `~` — the first stops a LaTeX run outside math mode, the second quietly becomes a non-breaking space. Records without a title are skipped and counted.

### A readable overview: `--report`

JSONL and CSV are for programs and the terminal summary scrolls away, while what a literature search actually has to hand over is prose. `--report` writes the merged records as a Markdown overview you can paste into a first draft:

```sh
scholar-digest out/*.jsonl --report out/report.md --report-title "Graph attention networks: a first pass"
```

It contains the size of the collection at a glance (records, total citations, year span, venues, first authors), the most-cited works with their original links, two grouped tables — by venue and by first author, each with records, citations, median, year span and the group's most-cited work — a text bar chart of records per year (which survives copy-paste), which query each record came from, and finally a "how much of this to trust" section that reuses the `--audit` checks to state the missing rates and doubtful fields outright.

A real excerpt (20 records; `--report-top 3` only to keep it short here):

```markdown
# Graph neural network surveys: a first pass

Built from 20 records collected with [google-scholar-crawler](...). Every number below comes
from what Scholar showed when the records were collected; nothing was re-fetched.

## At a glance

- **20 records**, 38,514 citations in total
- published **2020–2026**
- **17 venues**, **20 first authors**

## Most cited works (top 3)

| Citations | Year | Work | Venue |
| --- | --- | --- | --- |
| 17,842 | 2020 | [A comprehensive survey on graph neural networks](...) | … on neural networks … |
| 10,569 | 2020 | [Graph neural networks: A review of methods and applications](...) | AI open |
| 2,576 | 2022 | [Graph neural networks in recommender systems: a survey](...) | ACM computing surveys |

## When it was published

2022  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 5
2023  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 5
2024  ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇ 4

## How much of this to trust

| warn | venue truncated | 12 (60%) | Scholar elided the venue, ... |
| warn | authors truncated | 12 (60%) | Scholar elided the author list, ... |
```

The report opens by saying that every number comes from what Scholar showed when the records were collected and that nothing was re-fetched, so nobody mistakes it for live data.

Markdown punctuation in a title is escaped. `*SEM 2021`, `C*-algebras`, `[Re] ...` and `word2vec_extended` are real titles, and unescaped a renderer turns them into emphasis, code spans or broken links — the report then shows a title nobody collected. Link destinations are wrapped in angle brackets (`[title](<url>)`) because Scholar URLs carry parentheses and commas, which would end the link early.

### Who cites whom inside the collection: `--network`

A `--cites X` listing means every record on it cites the work whose citing-works id is `X`, and each record's own `cited_by_url` carries that id for itself. The citation relation is therefore already in the JSONL a crawl wrote: no extra request, and collections made before this existed still yield their graph.

```sh
$ scholar-digest out/graph.jsonl --network
  38 records and 0 uncollected works, 28 edges
  10 component(s), largest 11 works; 7 record(s) neither cite nor are cited here
  most cited from inside this collection:
      10 here     41,135 on Scholar  Graph attention networks
       9 here      4,408 on Scholar  Heterogeneous graph attention network
```

"From inside this collection" is the point: `10 here` means ten of these 38 records cite it, while `41,135 on Scholar` is its global count. Only the first says how central it is **to the topic you collected**.

Two limits, stated plainly:

- Seeding with `--cites <id>` means the cited work itself is not in the collection. Such targets are counted as `uncollected work <id>`, because otherwise a collection with no edges at all would look broken.
- One paper can appear under several `--cites` listings while merging keeps only one of its `query` values. Edges are therefore taken from every observation **before** merging, and nodes from the merged, filtered set, so deduplication cannot drop an edge.

A keyword-only collection has no citation edges, and the report says exactly that instead of drawing an empty graph.

### A folder as one collection: `--collection` and `--since`

After a few weeks a topic is a pile of files under `out/`, and remembering which one came from which session — and which one was last run's merge — is bookkeeping nobody should do by hand. `--collection` makes the folder the unit, and `--since` answers what changed:

```sh
$ scholar-digest --collection out --since out/merged.jsonl -o out/merged.jsonl
[in] 11 records from 2 file(s), 3 duplicates merged, 0 filtered out
  ...
  6 works since out/merged.jsonl -> 8 now: 2 new, 0 no longer here, 2 with a new citation count
  citations gained across the works in both: +41
  biggest movers:
    +    40  now      140  Work 0
    +     1  now      111  Work 1
  new:
    Work 6
    Work 7
[out] 8 records -> out/merged.jsonl
```

There is a trap `--collection` exists for: run `scholar-digest out/*.jsonl -o out/merged.jsonl` a second time and the glob **includes the merge it wrote last time**. Deduplication hides the damage, but the "how many files, how many duplicates" line stops meaning anything, and a collection that reads its own output back always looks complete. `--collection` excludes the files this run writes (`-o` and `--since`), which is what `11 records from 2 file(s)` above proves: the folder holds three `.jsonl` files and two were read.

`--since` keys both sides exactly as the crawler deduplicates, so a work stays the same work when its count, venue or snippet changed. The three outcomes mean:

- **new** — in this run's inputs, absent from the earlier merge.
- **with a new citation count** — present on both sides with a different number, sorted by how far it moved. A fall is reported as a fall (`-32`); Scholar does revise counts down. Note that merging keeps the higher count when one work appears in several inputs, so a fall shows only when the current inputs really report fewer.
- **no longer here** — in the earlier merge, absent now. This is **not** Scholar dropping a paper: its file was moved away, or the current filters (`--min-citations`, `--year-from`) now exclude it. The report says so in place, so the line cannot be misread as data loss.

When nothing moved, that is one line: `nothing changed since out/merged.jsonl: the same 20 works, same counts`.

Together with the refresh loop above, keeping a collection current is three commands and no mental bookkeeping:

```sh
scholar-digest --collection out --stale 60 --refresh-list out/refresh.txt   # offline: what to collect again
scholar-crawler --clusters-file out/refresh.txt -p 1 -o out/refresh-1.jsonl # one page load per id
scholar-digest --collection out --since out/merged.jsonl -o out/merged.jsonl --min-citations 1
```

Other files may sit in the folder: only `.jsonl` is read and subdirectories are not walked. Named files still work and are read after the folder.

### Keeping a collection current: `--stale` and `--refresh-list`

Every record carries `fetched_at`, the UTC moment it was collected, so how old a set is can be answered offline. Citation counts only grow, and the number stored three months ago is no longer quotable.

```sh
$ scholar-digest out/*.jsonl --stale 60 --refresh-list out/refresh.txt --refresh-limit 5
  20 records, collected between 475 and 0 days ago
  17 older than 60 days (85% of the set)
  17 of those can be re-listed by id, one page load each; 0 would need their query re-run
    375d      3,205 citations  --cluster 16121581283781234537 Kgat: Knowledge graph attention network…
    475d        203 citations  --cluster 13239932653767095002 Crystal graph attention networks…
[out] 5 id(s) to re-list -> out/refresh.txt (of 17 records older than 60 days)
```

The order is not age alone: a paper with three citations gains none in a year, while one with forty thousand drifts by hundreds in two months. The weight is age × log(citations), which puts the records whose numbers actually moved first. It orders a list for a human; it does not claim to predict the new count.

Right after one crawl every record carries the same `fetched_at`, age separates nothing, and the order degenerates into citation count descending. The report says so itself (`all the same age, so this order is by citation count, not by what moved`), so "whose numbers moved" is never read as age having had a say.

The file `--refresh-list` writes is the format `scholar-crawler --clusters-file` reads, so the loop closes:

```sh
scholar-digest out/*.jsonl --stale 60 --refresh-list out/refresh.txt   # offline: pick what to redo
scholar-crawler --clusters-file out/refresh.txt -p 1 -o out/new.jsonl  # one page load each
scholar-digest out/*.jsonl out/new.jsonl --min-citations 1 -o out/library.jsonl
```

Why the last line filters: `--cluster` lists *all versions* of a work, so besides the canonical record it returns the mirrors and preprints of the same paper as extra rows that carry no `data-cid` and no citation count. Five refreshes brought back 37 records here, 32 of them such version rows; `--min-citations 1` drops exactly those and leaves the 20 canonical records.

Merging was corrected to match: the richer record still wins, but fields it lacks are now filled from the other copy. A re-collected record carries the fresher count while a versions listing carries no snippet, and replacing the record wholesale would throw away what was already collected.

### Auditing what you collected: `--audit`

A Scholar card carries one grey line holding "authors - venue, year - site", and the parser splits it by position. That works for the usual card and fails quietly on the rest: a venue that is really a page range, a year taken from digits in the journal name, an author list Scholar itself truncated. Nothing downstream notices — `--group-by year` simply groups a wrong year.

`--audit` reads local files only and measures how much of what you already collected can be trusted:

```
$ scholar-digest out/g.jsonl --audit
  audit of 20 records: 2 checks tripped (0 errors, 2 warnings)
    warn  venue_truncated               12  60.0%  Scholar elided the venue, so a bibliography would cite '… on neural networks …'
        e.g. … on neural networks … | A comprehensive survey on graph neural networks
        e.g. … Computing Surveys … | Computing graph neural networks: A survey from algo…
    warn  authors_truncated             12  60.0%  Scholar elided the author list, so BibTeX gets 'and others'
        e.g. Z Wu, S Pan, F Chen, G Long… | A comprehensive survey on graph neural networks
        e.g. J Zhou, G Cui, S Hu, Z Zhang, C Yang, Z Liu, L Wang… | Graph neural networks: A review…
```

A clean set is one line, so there is nothing to read:

```
$ scholar-digest out/clean.jsonl --audit
  audit of 10 records: nothing implausible found
```

Two severities: `error` means the value is wrong (an implausible year, a year that appears nowhere in the byline it was read from, a venue that is a volume/issue/page range, a venue that still contains a year, a citation count with no citing-works link, a negative count, a missing title), and `warn` means missing or lossy (no venue/year/authors, a truncated author list, **a venue Scholar elided**, a bare hostname as the venue, a `[PDF]` tag left on the title, no card id). Records from an author profile are not counted as missing a card id: Scholar's profile rows carry no `data-cid` by construction, an export resolves them through the profile id they do carry at one extra page load, and calling that a defect made a whole author collection look 100% broken while pointing at a fix that does not exist. Each finding reports the count, the share and two real examples — not a score, but enough to judge whether the batch is usable.

That 60% is a real measurement: a Scholar result page elides long venue names from both ends (`… on neural networks …`), the full name is simply not on the page, and an exported BibTeX copies the elided form into `journal`. Summaries and grouped listings **keep that ellipsis** (`IEEE Transactions on Knowledge and Data …`): dropping it would name a journal that does not exist, since the real one is `… and Data Engineering`. It is not a parsing bug and cannot be repaired locally, so the audit's job is to count it and point at it — fix those entries by hand from this list, or pass `--bibtex` to take the full entry from Scholar's Cite popup at two extra page loads each.

Its first run found a real defect: the profile parser kept the year inside the venue (`Advances in neural information processing systems 27, 2014`) while the result-page parser stripped it. Grouping happened to be immune (`normalize_venue` cuts the volume tail), but the stored field disagreed between the two sources and the exported BibTeX repeated the year inside `journal`. Both parsing paths now share one stripping function.

### Auditing while crawling

Running `--audit` afterwards finds spoiled data, but by then the pages have been fetched. So every run applies the same checks to the records **it just wrote** (counting per check as they go past, no memory growth), says nothing at all in the normal case, and speaks up after the run summary only when an error-severity check matches at least 3 records and at least 20% of them:

```
[out] 40 new records (0 duplicates skipped) -> out/results.jsonl
[run] 5 requests in 1m, 0 takeovers, 0 navigation retries, delay now 4.0-11.0s
[audit] 1 field(s) parsed badly for a large share of this run's records — Scholar's layout may have changed
[audit]   venue_looks_like_pages: 16 of 40 records (40%) — venue is a volume, issue or page range, so venue grouping is wrong
[audit]       e.g. 521 (7553), 436-444 | Deep learning
[audit] run --self-check to test the parser, or scholar-digest --audit for the details
```

The thresholds exist so the run does not cry wolf: a single odd record (Scholar has plenty) stays quiet, and only a field failing across a large share of one run — usually a layout change — speaks. Missing-field warnings never raise an alarm, because Scholar withholding a venue or truncating an author list is not a parse failure.

### Grouping

`--group-by` splits the merged records by first author, venue, year or citation-graph level, ranked by total citations:

```
$ scholar-digest out/all.jsonl --group-by venue --groups 4
  by venue                                 count  citations  median  years      most cited
    Advances in neural information processi…    1     119743  119743  2014       Generative adversarial nets
    nature                                       1     118913  118913  2015       Deep learning
    arXiv preprint                               2      44564   22282  2017-2021  Graph attention networks
    The world wide web                           1       4408    4408  2019       Heterogeneous graph attention network
    ... and 4 more groups
```

The `median` column is there for fair comparison: it tells a group carried by one runaway paper apart from a group that is well cited throughout.

Two normalizations keep one venue from being split across groups: every arXiv preprint becomes `arXiv preprint` (Scholar writes the identifier into the venue), and profile-style venues like `nature 521 (7553), 436-444, 2015` lose the volume and pages to become `nature`. Grouping is case-insensitive and displays the first spelling seen. The overview's venue list uses the same normalization.

## The takeover log

The human takeover is the rarest, most important and least reproducible step in this tool: it happens while you are busy solving a CAPTCHA, and whatever scrolled past in the terminal is gone afterwards. So every takeover appends a record (`out/challenges.jsonl` by default), which `--show-state` reads back:

```sh
$ scholar-crawler --show-state
[state] 3 targets in out/state.json (1 finished)
[state]   attention is all you need [en] — next offset 30, 2026-09-02 10:45:51 UTC
[handoff] 2 takeovers in out/challenges.jsonl (captcha x2)
[handoff]   2026-09-02T12:26:23+00:00  captcha -> unattended, waited 6s (on request 11, loading 20)
[handoff]     matched form#captcha-form at about:blank
```

Each record carries 10 fields: the time `at`, the kind `kind` (`captcha`, `rate_limit`, `consent`), what the detector matched `reason`, the redacted URL `url`, which request of the run was blocked `request_index` (counting the blocked one), whether challenges arrived back to back and which one this was `consecutive`, how long it waited for a human `waited`, the target being fetched `target`, which kinds the window showed while they worked `saw` (`became sign_in`), and how it ended `outcome` — `resolved` (solved, crawling continued), `unattended` (`--headless` refusal or the wait timed out), `budget` (`--max-handoffs` exhausted), `interrupted` (Ctrl+C) or `rehearsed` (a drill).

That answers the questions that actually matter afterwards: at which request the block arrived, whether solving one was immediately followed by another (the pacing is still too fast), or whether nobody was at the keyboard.

A run that was actually stopped by a takeover names this file in the `files` section of its `--json` document, as `challenges`. A takeover is the one event a program cannot reconstruct from the document — it happened in a browser window while a person worked — so the document points at the evidence; a run with no takeover lists nothing, rather than sending a caller to read an empty file.

**The URL is redacted before it is written.** On a `/sorry/` challenge page `q` is the challenge token rather than a search query, so only `hl` survives there; on an ordinary result URL the parameters that describe the request (`q`, `start`, `cites`, `cluster`, `user`, …) are kept and signed ones like `scisig` become `REDACTED`. The file is safe to keep and to share.

`--rehearse-handoff` writes a record too, which also proves the log path is writable before a real challenge depends on it. A completed drill records `rehearsed`, but one nobody attends times out as `unattended` and one ended with Ctrl+C records `interrupted` — the documented way to end a drill early. So a drill is recognized by its `target` of `rehearsal`, never by its outcome, and `--show-state` prints `(drill)` on those lines. Otherwise walking away from a drill would slow every later run and claim this profile gets blocked at request 0.

### Slowing down within a run

The slowdown has two stages: every takeover widens the delays by `--backoff-factor`, and a challenge that arrives with **no successful page in between** — meaning solving the first one did not restore trust — waits out `--challenge-cooldown` before resuming, doubling for a third consecutive challenge and so on. `--max-handoffs` still aborts the run outright.

### Learning to slow down across runs

Getting blocked once should not stay in that one run. By default every start-up reads the takeover log and folds the lesson into this run's starting rhythm:

```
$ scholar-crawler -q "graph attention networks" -p 5
[pace] 3 previous blocks (captcha x2, rate_limit x1); typically at request 14; 1 arrived back to back; last 2026-09-02T12:37:20+00:00; starting at 6.8-18.7s (x1.7)
```

The rules are deliberately conservative, and they **only ever widen the delays** — history can prove a rhythm was too fast, but no history proves a faster one is safe:

- One block is not a pattern: it warns and changes nothing.
- Two or more blocks: x1.3; five or more: x1.6.
- A block that arrived with no successful page in between: +0.2 (solving the first one did not restore trust).
- Blocks typically arriving within the first 30 requests: +0.2 (the rhythm is the problem, not the volume).
- The terms sum to at most x2.0. That is the sum, not a separate clamp; a test enumerates every combination to keep the sentence true. Rehearsals are not evidence, however they ended.

Delays you passed yourself are never overridden: the history is printed and the run states that it keeps your values. `--no-learn-from-history` turns the behavior off entirely. `--dry-run` estimates with the learned rhythm, so you can see how much slower this run will be before starting it.

## Reviewing and resetting resume state

After many short sessions the state file no longer tells you much: its keys are signatures meant for the program. Both of these commands work offline:

```sh
$ scholar-crawler --show-state --state out/state.json
[state] 3 targets in out/state.json (1 finished)
[state]   attention is all you need [en] — next offset 30, 2026-09-02 10:45:51 UTC
[state]   cites:2960712678066186980 [en] — done after 50 records, 2026-09-02 10:45:51 UTC
[state]   author:kukA0LcAAAAJ [en] — next offset 100, 2026-09-02 10:45:51 UTC

# crawl a target from the start again (every signature containing the substring is
# dropped; an empty pattern drops all of them)
$ scholar-crawler --forget "attention" --state out/state.json
```

Signatures are rendered back into their targets, with the filters that distinguish them — year range, language, sort order, `--review-only` — in brackets, because the same query under different filters is a different cursor. Entries now also carry an update time; state files written by older versions still load and show `unknown time`.

A target cut short by `-n/--max-results` no longer counts as finished: stopping there was our decision and Scholar still had results, so its cursor stays resumable.

## Settings files: `--config`

Collecting one topic over weeks means retyping `--min-delay --max-delay --profile --follow-cites --year-from` every session, and a mistyped delay costs a request. Put those choices in a TOML file and pass it:

```sh
cp scholar.toml.example scholar.toml   # then edit it
scholar-crawler --config scholar.toml
```

Precedence is one rule and it is not negotiable: **command line > file > built-in defaults**. So "same settings, different query" is:

```sh
scholar-crawler --config scholar.toml -q "another topic" --pages 1
```

`--dry-run` names where every value in effect came from, which is what makes "why was the delay 8 seconds?" answerable later:

```
[explain] settings file scholar.toml: 5 value(s) in effect
[explain]   cooldown_every, max_delay, out, profile, query
[explain]   min_delay came from the command line instead, which wins over the file
[explain]   pages came from the command line instead, which wins over the file
```

An ordinary run prints one line instead: `[config] 5 setting(s) from scholar.toml, 2 overridden by flags`.

How a file is written:

- **A key is the long flag without its dashes.** `min-delay` and `min_delay` both work, and so does `"--min-delay"`.
- **Tables like `[pacing]` are for the reader only.** The crawler reads their contents exactly as if the keys stood at the top of the file, so organise a file however you like without memorising which flag belongs to which group.
- **A repeatable flag takes an array** (`query = ["a", "b"]`). Passing `-q` on the command line *replaces* that list rather than adding to it — which is what "same settings, different query" needs.
- **Modes may not live in a file.** `--doctor`, `--self-check`, `--rehearse-handoff`, `--show-state`, `--forget`, `--dry-run`, `--recipes` and `--config` decide *what the command does*, not how it behaves. A settings file naming one is an error: it should not be able to turn a crawl into something else behind your back.

Anything wrong is reported before the first request, by name:

```
error: scholar.toml: unknown setting 'min_dely'; did you mean 'min_delay'?
error: scholar.toml: 'pages' wants a number, not a string
error: scholar.toml: 'query' wants a list of values
error: scholar.toml: 'headless' wants true or false
error: scholar.toml: 'doctor' decides what the command does, so it stays on the command line
error: scholar.toml: [pacing.deeper] nests too deep; settings are one level
error: scholar.toml: 'min-delay' is set twice
```

`tomllib` is stdlib from Python 3.11; 3.10 needs `tomli`, which `pyproject.toml` declares for `python_version < "3.11"`. `--doctor` reports a `settings files` line either way, so a missing reader surfaces before a `--config` run needs it.

## Reading the command back and costing it: `--dry-run`

There are more than fifty flags, and a wrong combination rarely fails — it quietly does something else; and `--pages`, `-n`, `--follow-cites` and `--bibtex` multiply, which makes it easy to start a run that takes hours. `--dry-run` sends nothing, translates the command into plain words (what it crawls, how deep, at what rhythm, what a challenge does, which files it touches), names the flags that contradict or cancel each other, and then bills it:

```sh
$ scholar-crawler -q "graph attention networks" -p 3 --bibtex out/refs.bib --dry-run
[explain] crawling 1 listing(s)
[explain]   target: graph attention networks
[explain] up to 3 page(s) per listing, 10 records a page
[explain] waiting 4–11s between page loads
[explain] pausing 90s every 10 loads, and giving up on a page after 45s
[explain] on a challenge: the window is brought to you, waiting up to 600s for you to clear it, up to 5 time(s) this run
[explain] after each takeover the delays widen by x1.6
[explain] creating records: out/results.jsonl
[explain] creating bibtex: out/refs.bib
[explain] creating resume state: out/state.json
[explain] creating takeover log: out/challenges.jsonl
[plan] graph attention networks -> https://scholar.google.com/scholar?hl=en&q=graph+attention+networks&as_vis=0&as_sdt=0%2C5
[plan] seed targets: 3 page loads, up to 30 records
[plan] bibtex export: up to 60 page loads
[plan] total: up to 63 page loads for 30 records
[plan] estimated 20 min at 4-11s between requests plus 6 cooldowns of 90s
[plan] nothing was requested; drop --dry-run to start
```

What it catches (`warn` means a flag does not do what it looks like; `note` means a consequence worth knowing):

- `--headless` has nobody to hand a challenge to, so the first one ends the run with whatever was collected;
- `--year-from` later than `--year-to`, which returns nothing;
- values that amount to doing no work, such as `--pages 0` or `--max-handoffs 0`;
- delays shorter than the default 4–11s, and `--cooldown-every 0` removing the long pause;
- `--no-learn-from-history` when the takeover log actually holds history (silent when it does not);
- `--resume` with no cursor for these targets, which really means starting over, and `--resume` together with `--start`, where the cursor wins (a stored cursor without `--resume` needs no `--dry-run`: every run says so before it starts);
- two output flags pointed at one file;
- `--bibtex` with `--author` costing three page loads per record, `--dump-html` writing pages that carry session material to disk, `--proxy` addresses being challenged more, and a `--host` other than the default.

The `[explain]` lines answer "is this the command I meant?" and the `[plan]` lines answer "what will it cost?". Those were two flags (`--explain` and `--dry-run`), and nobody ever wanted only one of them: neither sends a request, both need targets, and each is half the answer to "should I start this run?".

Here is a run whose cost gets away from you:

```sh
$ scholar-crawler -q "diffusion models" -q "flow matching" -p 3 \
    --follow-cites 1 --follow-breadth 4 --bibtex out/x.bib --dry-run
[plan] diffusion models -> https://scholar.google.com/scholar?hl=en&q=diffusion+models&as_vis=0&as_sdt=0%2C5
[plan] flow matching -> https://scholar.google.com/scholar?hl=en&q=flow+matching&as_vis=0&as_sdt=0%2C5
[plan] seed targets: 6 page loads, up to 60 records
[plan] citation expansion: up to 8 listings, 24 page loads, up to 240 records
[plan] bibtex export: up to 600 page loads
[plan] total: up to 630 page loads for 300 records
[plan] estimated 3.4 h at 4-11s between requests plus 63 cooldowns of 90s
[plan] nothing was requested; drop --dry-run to start
```

Every number is an upper bound: listings that run out of results and expansions with nothing left to expand cost less. Bad arguments still fail under `--dry-run`, so it doubles as an argument check.

## Self-check

When results come back empty and you suspect a Scholar layout change, spend one request on the self-check:

```sh
$ scholar-crawler --self-check
[check] fetching one page for 'machine learning'
[check] results_parsed   ok    10 records on the page
[check] titles           ok    10/10 have a title
[check] links            ok    10/10 non-citation records have a link
[check] bylines          ok    10/10 have an author line
[check] years            ok    10/10 have a year
[check] snippets         ok    10/10 have a snippet
[check] card_ids         ok    10/10 carry Scholar's data-cid (needed for BibTeX)
[check] citation_counts  ok    10/10 link their citing works
[check] total_estimate   ok    result count read as 6010000
[check] pagination       ok    next-page link found
[check] all 10 checks passed
```

It fetches one page of a broad query and reports, field by field, whether titles, links, author lines, years, snippets, `data-cid`s, citing-works links, the result count and the next-page link still parse. Exit code 0 means everything held; any `x` exits 1 and points at `--dump-html` for a copy of the page. The output above is a real run; when Scholar changes its markup, `card_ids` and `citation_counts` are usually the first to go.

## Options

`--help` opens with the three shapes a run takes (a search, an id, an offline mode) and closes with four lines saying which of the four collect-nothing modes answers which question, rather than filling a screen with fifty flags; the full list follows it in groups, and the table below is the same set arranged by purpose.

| Option | Meaning |
| --- | --- |
| `-q/--query`, `--queries-file` | keyword search, repeatable; file holds one query per line |
| `--cites`, `--cluster` | crawl citing works / all versions of one work; accepts a numeric id or a `cited_by_url`/`versions_url`, repeatable |
| `--author` | crawl an author's publication list; accepts a 12-character user id or a profile URL, repeatable; `--sort-by-date` orders by year |
| `-p/--pages`, `-n/--max-results` | pages per entry point / hard result cap (last page truncated exactly). Search pages hold 10 results, profile pages 100 publications |
| `--follow-cites`, `--follow-breadth`, `--follow-min-citations` | after the seed entry points, keep crawling the works that cite them for this many levels; each level expands only the most-cited N records, skipping anything below the citation floor |
| `--start`, `--resume` | first offset; continue from the saved cursor |
| `--year-from/--year-to`, `--sort-by-date`, `--review-only` | year range, date order, reviews only |
| `--no-citations`, `--no-patents` | exclude citation-only records / patents |
| `--lang`, `--host` | interface language (`hl`), which the browser's `Accept-Language` follows with no separate flag; mirror such as `https://scholar.google.de` |
| `--challenge-log` | takeover log (default `out/challenges.jsonl`, URLs redacted) |
| `-o/--out`, `--state` | JSONL output and resume state (CSV is `scholar-digest --csv`; a crawl does not export tables) |
| `--bibtex` | also export BibTeX to a `.bib` file; deduplicated by citation key, with `extra.bibtex_key` recorded on each record |
| `--dump-html` | raw HTML of every fetched page |
| `--profile`, `--channel`, `--timezone`, `--proxy` | browser profile and environment |
| `--no-learn-from-history` | start at the default rhythm instead of reading the takeover log |
| `--min-delay/--max-delay`, `--cooldown-every/--cooldown-seconds` | request rhythm |
| `--handoff-timeout`, `--max-handoffs`, `--backoff-factor`, `--challenge-cooldown` | how long to wait for a human (0 = forever), takeover budget, slowdown per takeover, wait-out after back-to-back challenges |
| `--recipes` | print complete commands to copy (no requests) |
| `--config FILE` | read settings from a TOML file; anything passed as a flag wins over it |
| `--show-state`, `--forget PATTERN` | review stored progress and recent takeovers; drop cursors by signature substring (empty pattern drops all) |
| `--dry-run` | read the command back, name flags that cancel each other, print the plan and duration estimate, then stop without requesting anything |
| `--self-check` | run the parser self-check (one request) and report field by field what still parses |
| `--headless` | no window; **a challenge then aborts the run with instructions** |

`--headless` and human takeover are mutually exclusive by nature: with no window, nobody can act. Run headed once so a human clears the challenge, then reuse the same `--profile` in headless mode; if it is still blocked, the run exits with a clear message instead of spinning.

Invalid pacing (negative values, `--min-delay` above `--max-delay`, `--backoff-factor` below 1) fails at startup rather than producing strange timing.

## Output

One JSON object per line:

```json
{"cluster_id":"7997180733303660440","position":1,"title":"Attention is all you need",
 "link":"https://proceedings.neurips.cc/...","resource_link":"https://.../paper.pdf","resource_type":"PDF",
 "authors":"A Vaswani, N Shazeer, N Parmar","venue":"Advances in neural information processing systems",
 "year":2017,"cited_by_count":123456,"cited_by_url":"https://scholar.google.com/scholar?cites=...",
 "versions_count":89,"versions_url":"...","related_url":"...","citation_only":false,
 "snippet":"We propose a new simple network architecture ...","query":"...","page_start":0,"fetched_at":"..."}
```

Records are deduplicated by `cluster_id` (falling back to title + link) across pages and across runs, so appending to the same file never produces duplicate lines. The `query` field records the entry point: the keyword query, or `cites:<id>` / `cluster:<id>` / `author:<id>`.

Author publications land in the same JSONL (with Scholar's citation id in `extra.citation_id`); the profile header goes next to `-o` as `<name>.profiles.jsonl` (`-o out/bengio.jsonl` gives `out/bengio.profiles.jsonl`, one line per author, replaced on a re-crawl). It has no flag of its own: an author crawl parses that header anyway, and naming it after `-o` keeps two runs from quietly sharing one profile file:

```json
{"user_id":"kukA0LcAAAAJ","name":"Yoshua Bengio",
 "affiliation":"Professor of computer science, University of Montreal, Mila, IVADO, CIFAR",
 "organization":"University of Montreal","homepage":"https://yoshuabengio.org/",
 "verified_email":"Verified email at umontreal.ca",
 "interests":["Machine learning","deep learning","artificial intelligence"],
 "cited_by_total":1149112,"cited_by_recent":764217,"h_index":259,"h_index_recent":208,
 "i10_index":1106,"i10_index_recent":947,"fetched_at":"..."}
```

## Crawling outward along citations: `--follow-cites`

`--follow-cites DEPTH` takes the records already collected, keeps the `--follow-breadth` most-cited of them, opens each one's "Cited by" listing, and repeats for the requested number of levels.

- Requests grow multiplicatively: one seed at depth 2 and breadth 5 is up to 31 listings, each still paged by `-p`. The worst-case count is printed before the run starts.
- Every cites id is crawled at most once per run, so repeated branches are skipped; each record records the level it came from in `extra.follow_depth`.
- Expanded listings inherit the year, language and sorting filters given on the command line, and `--resume` tracks each one by its own signature.
- Publications collected from an author profile can seed the expansion too.

## Exporting BibTeX while crawling: `--bibtex`

`--bibtex` costs two extra page loads per record: Scholar's "Cite" popup, then the signed `scholar.bib` link inside it (the signature cannot be constructed locally). So:

- A 10-result page becomes 21 requests instead of 1, an order of magnitude slower, with a matching rise in challenge risk. Pair it with `-n` and export only what you will cite.
- Both loads are ordinary navigations in the visible window, so pacing and human takeover cover them. A background HTTP request is not an option: Scholar answers 429 to the same request issued outside the browser's navigation stack.
- Author-profile publications carry no Scholar `data-cid`, so it is resolved through their cluster listing first: three loads per record instead of two, announced once at the start.
- `scholar-digest --bibtex`, by contrast, sends nothing and synthesizes entries from stored fields, so a venue keeps the elided form the result page showed (`journal = {… on neural networks …}`). Before citing formally, run `--audit` to see how many records `venue_truncated` covers, then decide between fixing them by hand and spending two loads on Scholar's own entry.

## Rehearsing the human takeover

A real CAPTCHA cannot be summoned on demand, so the whole takeover path can be rehearsed against a locally generated page (**no request is sent**):

```sh
scholar-crawler --rehearse-handoff
```

The flow is the one a real challenge triggers: the page is detected as a challenge, the bell rings, the window comes to the front, the takeover notice prints, and the wait polls. Press the button on the page to stand in for solving it — the rehearsal then confirms the page reads as content again and reports how long the wait took, exiting 0. With nobody acting it fails at `--handoff-timeout` (exit 1), and with `--headless` it verifies the "no window, so refuse" path instead.

## Run summary

Every run ends with a one-line summary — after a normal finish, a Ctrl+C, or a failure alike:

```
[run] 12 requests in 3.4 min (3.5/min), 1 takeover (captcha x1), 0 navigation retries, delay now 6.4-17.6s
```

The request count includes cite popups and BibTeX exports, takeovers are broken down by kind, and `delay now` is the rhythm after any backoff — noticeably above your starting values means this run was challenged and the address or the pacing needs to be more conservative. Runs shorter than 30 seconds report seconds instead of a rate, which would mostly measure start-up.

## Failures in plain words

The least useful thing a stopped run can print is Playwright's call log. Every failure is translated into what happened and what to do next, with the raw error kept underneath:

```
$ scholar-crawler -q "graph attention networks"

[stop] the host refused the connection, so nothing was crawled (https://scholar.google.com/scholar?...)
[stop] try: open the same address in a normal browser: if that fails too, the network is blocking it
[stop] try: check --host if you pointed it somewhere other than scholar.google.com
[stop] try: check whether a VPN, firewall or corporate proxy is in the way
[stop] underlying error: Page.goto: net::ERR_CONNECTION_REFUSED at https://scholar.google.com/...
```

It also stops wasting time: a refused connection, an unresolvable name, a rejected certificate and a refusing proxy answer the same way every time, so they stop immediately instead of retrying three times over 15 seconds; only a timeout, a dropped connection or a lost network is retried.

Told apart: a refused connection, a name that does not resolve, no internet at all, a proxy that refuses, a connection closed mid-request (how networks usually drop automated traffic — slow down and retry), a rejected certificate (something is intercepting HTTPS, or this machine's clock is wrong), a load that timed out (`--nav-timeout` raises the limit), a browser window closed early, HTTP 429 or 503 from Scholar (a refusal to serve, not a bug: wait, then `--resume`), any other 4xx/5xx, and a page that loaded while carrying none of Scholar's markers. Only the error's first line is kept, so the call log no longer fills the screen — and it is still there when the diagnosis guesses wrong.

One behaviour was corrected along the way: a page that loaded with none of Scholar's markers used to be treated as "this query has no results", so a captive-portal login or an unfamiliar layout looked like an unwritten topic — and the run kept paging. Such a page now stops the run and names `--self-check` and the saved copy. A genuine zero-hit listing is still just empty: Scholar's own "did not match any articles" notice is content, parsed as zero records.

## Getting challenged less

- Do not shrink the default delays; rhythm, not the User-Agent, is what gets a client blocked.
- Reuse one long-lived profile instead of deleting `.scholar-profile` between runs.
- Keep a single run to tens of pages; spread large collections over days.
- For large-scale metadata, prefer sources with real APIs (Semantic Scholar, OpenAlex, Crossref) and keep this tool for what only Scholar has.

## Development

```sh
python3 -m pytest -q     # 511 tests, fully offline
ruff check .             # same lint configuration as CI
```

Every test is offline (no network). CI runs the same two commands on 3.10 and 3.13; separately, a clean venv installs the tool from this git URL and runs them against whatever dependency versions a new install resolves to — most recently playwright 1.62.0, bs4 4.15.0 and lxml 6.1.3, all newer than the development machine's, with the whole suite passing.

Grouped by what they cover; read `tests/` for the detail:

- **Parsing**: every field of result cards and author profiles, plus four real-page fixtures (`tests/pages/`) so parsing is both correct and still true to Scholar's markup; the nine records those two real pages yield are then fed to `--audit`, the overview, `--network` and `--stale`, because a report whose job is to judge real data has to hold up on real data first (no errors at all, and warnings only for what Scholar itself did). The file `--refresh-list` writes is then read back by `scholar-crawler --clusters-file … --dry-run`, so the loop the file's own comment promises is a test rather than a claim
- **Crawl loop**: paging, author batching, pacing and cooldowns, the quiet wait after back-to-back blocks, HTML dumps, run summaries
- **Human takeover**: challenge detection on real headless Chromium, waiting and timeouts, a closed window, a headless refusal, the takeover log and cross-run slowdown
- **End to end**: a real browser against a local fake Scholar (`tests/fakescholar.py`) — page budget, no loss across a takeover, `--resume`, author profiles, collected records surviving a headless refusal, and one run described entirely by a settings file
- **Failure diagnosis**: nine network failures classified apart, only plausibly transient ones retried, unrecognized errors keeping their text and still offering a next step
- **Output for people**: what `--doctor`, `--dry-run`, `--recipes`, `--audit` and `--report` say, and the planned load count matched against the real one
- **Output for programs**: the document's fixed keys, the failure vocabulary, the stdout/stderr split, and AGENTS.md matching that vocabulary word for word
- **Offline tools**: merging, filtering, summaries, grouping, bibliography synthesis, staleness and refresh lists, collection deltas
- **Configuration and interface**: settings-file equivalence and errors, both parsers (groups, help, defaults), and both READMEs' links and module lists
- **The commands in the documentation**: every `scholar-crawler`/`scholar-digest` command in both READMEs and AGENTS.md — each one that sends no request is actually run in a scratch directory (the test creates whatever files it reads); the rest must be registered in the test with the reason it cannot run offline (it crawls, `--self-check` costs one request, `--install-browser` downloads, `--rehearse-handoff` needs a person). A new command escapes neither branch
- **The output the documentation shows**: `--recipes` and `--dry-run` answer from the command line alone — no machine, no data — so every line quoted for them is compared verbatim and in order; for reports whose values vary (`--doctor`, the `scholar-digest` overview) the label column is compared instead (`browser`, `profile`, `citation-only`, `graph levels`, …), so renaming or dropping one turns the docs red; and every `[tag]` shown anywhere in the docs must be a channel the tool really prints

### Checking that the guards guard

A test that never fails and a test that cannot fail look the same from outside.
`tests/mutate.py` keeps a table of deliberate defects, each naming the file and line to
break, the wrong version, and the tests that must fail because of it:

```sh
python3 -m tests.mutate          # 62 entries, about 4 minutes; every file is restored after
python3 -m tests.mutate --all    # includes the one whose broken form waits out a real timeout
python3 -m tests.mutate offset   # only entries whose label matches
```

It rewrites source files and restores them, so do not run it over uncommitted work. Anything
no test noticed is listed at the end, and the exit code is 1. Every write drops the matching
`__pycache__`: swapping `0.2` for `0.6` keeps the file size, the restore usually lands in the
same second, and Python then treats the old `.pyc` as current — so the next run reads the wrong
bytecode and a sound test looks broken, or a broken one looks sound.

Several rounds of auditing built this table, and eight real holes came out of it: the
`--min-citations` threshold was inclusive by accident, nothing bounded the length of the
staleness list, "a crash never loses collected data" rested on a `flush()` no test read back,
`argparse.SUPPRESS` satisfied "every flag is explained", nothing checked that the results
selector still matches a Scholar page, `as_sdt=0` is a substring of `as_sdt=0,5` so the patent
switch could invert unnoticed, the terminal bell's own line was never executed, and only the
count threshold behind an audit alarm was doing any work.

The deliverables were checked with parsers from outside this project: `bibtexparser` read
`refs.bib`, `markdown-it-py` rendered `report.md`, and the stdlib `csv` module read `rows.csv`
back — over 10 real `C*-algebras` records. Every entry parsed, no key repeated, all 10 titles
came through the renderer verbatim, and the CSV round-tripped. Those two packages are **not**
dependencies of this project; they were installed for the audit alone
(`pip install bibtexparser markdown-it-py`). The two defects that check found — Markdown
punctuation in titles, and `^` and `~` in BibTeX — are now held by offline tests.

Two rules came out of the same exercise and now live in the tool: a mutation must match
exactly once in its file — otherwise it lands in a docstring and reports a hole that does not
exist — and the break must actually reach the file, so `audit()` raises when the text does not
match instead of quietly running a green suite. `check_table()` enforces the first, and a test
runs it in CI, since the audit edits sources and cannot live in the suite.

### Real-structure regression fixtures

`tests/pages/` holds sanitized copies of four pages the crawler really loaded: a result page, an author profile, a cite popup and a BibTeX export. Hand-written fixtures prove the parser's logic but not that it still fits Scholar's real markup; these do, entirely offline — the result page runs all ten `--self-check` checks.

`tests/sanitize.py` does the sanitizing on one rule: keep every bit of structure, drop every credential. It removes `<script>`, `<style>` and `<iframe>`, points image `src` at `about:blank`, replaces signed and session parameters (`scisig`, `xsrf`, `scisdr`, `usg`, …) with `REDACTED` both in plain URLs and nested inside encoded parameters such as `continue=`, blanks xsrf values in hidden inputs, rewrites `Verified email at ...` to `example.edu`, and trims repeated cards to a few. Class names, `data-cid`, `cites=`/`cluster=` links and markers like `scisf=4` are left exactly as Scholar served them. A dedicated test scans all four files for leftover scripts and un-redacted long tokens.

Refresh the fixtures when Scholar changes its layout:

```sh
scholar-crawler -q "graph attention networks" -p 1 -n 2 --bibtex out/x.bib \
    --dump-html out/dump -o out/d.jsonl
python3 -m tests.sanitize out/dump/<result-page>.html tests/pages/results.html 6
```

### End-to-end tests: a local fake Scholar

Unit tests feed HTML strings to the parser and `--self-check` needs the real network; neither covers the path that matters most: a **real browser** navigating real URLs, tripping a challenge, resuming after a takeover and writing the files correctly. `tests/fakescholar.py` serves a fake Scholar on loopback with `http.server`, answering only `/scholar` and `/citations`, and can be told to answer a given offset with a challenge page the first time it is asked. The stand-in human in the tests clears it the way a person does — reload the page, the challenge is gone, the crawl continues.

Behaviour that used to be verified once against the real Scholar now runs on every CI job: paging to the page budget and not one page further, all 20 records on disk, a cursor at 40, challenge → takeover → the same offset refetched → nothing lost, `resolved` in the takeover log with a redacted URL, `--resume` continuing from 20 to 40, an author profile and its header stored, clean data raising no audit alarm, when headless refuses the takeover, the 10 records already collected still on disk, exit code 1, and the cursor left at 10; Ctrl+C between two pages giving exit code 130 with kind `interrupted`, the 10 records kept, and the cursor at 10; and a batch whose second query hits a page this tool cannot read keeping the first query's 20 records and its cursor, leaving the failed target's cursor at 0 so `--resume` retries it, with both targets logged as `1/2` and `2/2`.

## Compliance

Google Scholar's terms do not allow automated scraping, and collected metadata remains the publishers' copyright. Use this at personal research scale, respect the target site's terms and `robots.txt`, and do not resell or redistribute scraped results. The deliberately slow pacing and the human-in-the-loop design are what keep this from being a mass-abuse tool.

## Layout

```
scholar_crawler/
  models.py     data structures for records and requests (search, result, profile, page)
  urls.py       query and profile URLs, filters, id/URL parsing
  parser.py     result-page and profile HTML -> structured records
  challenge.py  challenge detection + human takeover wait
  diagnose.py   failure diagnosis: network and page failures turned into next steps
  browser.py    persistent-profile browser session
  crawler.py    crawl loop: pacing, takeover, pagination and author batching, BibTeX loads, HTML dumps
  run.py        executing one run: opening the browser, crawling targets, expanding, reporting outputs
  modes.py      the five modes that replace a crawl: doctor, self-check, rehearsal, show and forget state
  doctor.py     environment check: dependency versions, browsers, directory permissions
  expand.py     citation-graph expansion: seed choice, caps, dedup
  explain.py    the command read back in plain words, with doubtful combinations
  plan.py       run plan: pages, loads and duration estimates
  selfcheck.py  parser self-check: per-field health report
  rehearsal.py  takeover rehearsal: local challenge page, full-path drill
  history.py    takeover log -> starting-rhythm advice
  recipes.py    complete commands to copy
  collection.py a folder as one collection: input discovery, diff against the last merge
  digest.py     offline digest: merge, filter, command line
  analysis.py   offline analysis: overview counts and grouping
  refresh.py    offline staleness: which records to collect again
  graph.py      offline citation graph: edges from collected records, who is cited most
  report.py     offline overview: the readable Markdown report
  audit.py      offline audit: implausible and missing fields
  bibsynth.py   offline bibliography: BibTeX from stored fields
  storage.py    JSONL writer, author profile records, the .bib file, resume state
  config.py     TOML settings files: reading, validation, precedence, provenance
  machine.py    the JSON document for programs: fixed keys, failure vocabulary, stdout discipline
  text.py       fitting text to a terminal column, marking every cut
  cli.py        command-line entry point: flag definitions and mode dispatch
  __main__.py   makes python3 -m scholar_crawler equivalent to scholar-crawler
tests/          offline tests, headless-Chromium detection, and mutate.py's guard audit
scholar.toml.example   sample settings file; copy it to scholar.toml
queries.example.txt    sample query list
```

MIT licensed.
