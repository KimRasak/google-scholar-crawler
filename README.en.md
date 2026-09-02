# Google Scholar crawler with human takeover

[![tests](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml/badge.svg)](https://github.com/KimRasak/google-scholar-crawler/actions/workflows/tests.yml)
English | [中文](README.md)

Runs academic searches on Google Scholar in a real browser (Playwright + your installed Chrome). Pagination, parsing and output are automated; the moment Google shows a reCAPTCHA, a `/sorry/` interstitial, a consent wall or a sign-in prompt, the crawler stops and hands the visible window to you, then resumes on its own once the page is a normal result page again.

The code never tries to solve, bypass or hide a verification challenge. Verification is done by a human, in a window they can see.

## How it works

1. Launches headed Chrome with a **persistent profile** (`--profile`, default `.scholar-profile`). Cookies earned by solving a challenge survive restarts, so takeovers get rarer over time.
2. Waits a random 4–11 seconds before every request, with a 90-second cooldown every 10 requests. The counter spans the whole run — across queries, across authors, including BibTeX loads — instead of resetting per query. A scroll and short dwell after load keep the rhythm off a machine grid.
3. Classifies every page after navigation:
   - challenge → terminal bell, printed URL, window raised to the front, page re-inspected every 2 seconds; once you clear it the crawl resumes and page delays are multiplied by `--backoff-factor` (default ×1.6), so a challenged run automatically slows down;
   - otherwise → parse the result page.
4. Appends results to JSONL and flushes per page, and records the next unfetched offset per query in a state file. Ctrl+C, a crash or a handoff timeout never loses collected data; `--resume` continues from the cursor.

Detection uses the URL (`/sorry/`, `consent.google.`, `accounts.google.com`), DOM selectors (`#gs_captcha_ccl`, `#gs_captcha_f`, `form#captcha-form`, reCAPTCHA iframes) and body text when no results are present. Citation and version counts are read from link hrefs, so parsing is independent of the interface language.

## Install

```sh
git clone https://github.com/KimRasak/google-scholar-crawler.git
cd google-scholar-crawler
python3 -m pip install -e .              # provides the scholar-crawler command
python3 -m playwright install chromium   # only if you do not use local Chrome (--channel "")
```

Python 3.10+. Keep the default `--channel chrome` when Chrome is installed. `scholar-crawler ...` and `python3 -m scholar_crawler ...` are equivalent.

## Usage

```sh
# Keyword search, three pages (10 results each)
scholar-crawler -q "large language model agents" -p 3 -o out/agents.jsonl

# Year filter, sorted by date, CSV export, capped at 40 records
scholar-crawler -q "retrieval augmented generation" \
  --year-from 2023 --sort-by-date -n 40 -o out/rag.jsonl --csv out/rag.csv

# Batch queries (one per line, # comments allowed) with resume
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
scholar-crawler --author kukA0LcAAAAJ -o out/bengio.jsonl --profiles-out out/profiles.jsonl
scholar-crawler --author "https://scholar.google.com/citations?user=kukA0LcAAAAJ&hl=en" --sort-by-date -p 2

# Scholar's advanced syntax goes straight into the query
scholar-crawler -q 'author:"Yoshua Bengio" source:"NeurIPS"' -p 2
```

A takeover looks like this:

```
[handoff] captcha: matched #gs_captcha_ccl
[handoff] URL: https://www.google.com/sorry/index?continue=...
[handoff] The browser window is yours. Solve the challenge ...
[handoff] cleared — resuming automated crawl.
[pace] backing off to 6.4-17.6s between pages
```

## Digesting collected results (no requests)

Crawling in short sessions leaves results spread over several JSONL files. `scholar-digest` reads local files only, and merges, filters, summarizes and exports them:

```sh
# merge several result files into one deduplicated set, plus a CSV
scholar-digest out/*.jsonl -o out/all.jsonl --csv out/all.csv

# keep only well-cited recent work
scholar-digest out/all.jsonl --min-citations 1000 --year-from 2018 -o out/hot.jsonl
```

By default it prints an overview: record count, total citations, how many already carry a BibTeX key, citation-only records, the year distribution, the citation-graph level distribution, the most frequent venues, and the most-cited records.

When the same work appears in several files, the higher citation count wins as the fresher observation, the fuller record wins ties, `extra.bibtex_key` survives, and `follow_depth` keeps the shallowest level.

| Option | Effect |
| --- | --- |
| `-o`, `--csv` | write the merged records as JSONL / CSV |
| `--min-citations`, `--year-from`, `--year-to` | filters; a year range drops records without a year |
| `--top` | how many most-cited records the overview lists (default: 5) |
| `--group-by` | group by `author`, `venue`, `year` or `level` |
| `--min-group`, `--groups` | hide groups below N records; how many groups to list (default: 10) |
| `--quiet` | print only what was written; needs `-o` or `--csv` |

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

## Counting the cost first: `--dry-run`

`--pages`, `-n`, `--follow-cites` and `--bibtex` multiply, which makes it easy to start a run that takes hours. `--dry-run` sends nothing and spells out what would be requested, the worst-case page loads, and roughly how long the current rhythm would need:

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

### Grouping

`--group-by` splits the merged records by first author, venue, year or citation-graph level, ranked by total citations:

```
$ scholar-digest out/all.jsonl --group-by venue --groups 4
  by venue                                 count  citations  median  years      most cited
    Advances in neural information processin     1     119743  119743  2014       Generative adversarial nets
    nature                                       1     118913  118913  2015       Deep learning
    arXiv preprint                               2      44564   22282  2017-2021  Graph attention networks
    The world wide web                           1       4408    4408  2019       Heterogeneous graph attention network
    ... and 4 more groups
```

The `median` column is there for fair comparison: it tells a group carried by one runaway paper apart from a group that is well cited throughout. `--min-group 3` folds away the long tail of one-off groups.

Two normalizations keep one venue from being split across groups: every arXiv preprint becomes `arXiv preprint` (Scholar writes the identifier into the venue), and profile-style venues like `nature 521 (7553), 436-444, 2015` lose the volume and pages to become `nature`. Grouping is case-insensitive and displays the first spelling seen. The overview's venue list uses the same normalization.

## Real-structure regression fixtures

`tests/pages/` holds sanitized copies of four pages the crawler really loaded: a result page, an author profile, a cite popup and a BibTeX export. Hand-written fixtures prove the parser's logic but not that it still fits Scholar's real markup; these do, entirely offline — the result page runs all ten `--self-check` checks.

`tests/sanitize.py` does the sanitizing on one rule: keep every bit of structure, drop every credential. It removes `<script>`, `<style>` and `<iframe>`, points image `src` at `about:blank`, replaces signed and session parameters (`scisig`, `xsrf`, `scisdr`, `usg`, …) with `REDACTED` both in plain URLs and nested inside encoded parameters such as `continue=`, blanks xsrf values in hidden inputs, rewrites `Verified email at ...` to `example.edu`, and trims repeated cards to a few. Class names, `data-cid`, `cites=`/`cluster=` links and markers like `scisf=4` are left exactly as Scholar served them. A dedicated test scans all four files for leftover scripts and un-redacted long tokens.

Refresh the fixtures when Scholar changes its layout:

```sh
scholar-crawler -q "graph attention networks" -p 1 -n 2 --bibtex out/x.bib \
    --dump-html out/dump -o out/d.jsonl
python3 -m tests.sanitize out/dump/<result-page>.html tests/pages/results.html 6
```

## Self-check

When results come back empty and you suspect a Scholar layout change, spend one request on the self-check:

```sh
scholar-crawler --self-check
```

It fetches one page of a broad query and reports, field by field, whether titles, links, author lines, years, snippets, `data-cid`s, citing-works links, the result count and the next-page link still parse. Exit code 0 means everything held; 1 lists what failed and points at `--dump-html`.

## Options

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
| `--lang`, `--host` | interface language (`hl`); mirror such as `https://scholar.google.de` |
| `-o/--out`, `--csv`, `--state` | JSONL output, CSV export, resume state |
| `--bibtex` | also export BibTeX to a `.bib` file; deduplicated by citation key, with `extra.bibtex_key` recorded on each record |
| `--profiles-out`, `--dump-html` | author profile headers (one record per author, re-crawls replace it), raw HTML of every fetched page |
| `--profile`, `--channel`, `--locale`, `--timezone`, `--proxy` | browser profile and environment |
| `--min-delay/--max-delay`, `--cooldown-every/--cooldown-seconds` | request rhythm |
| `--handoff-timeout`, `--max-handoffs`, `--backoff-factor`, `--challenge-cooldown` | how long to wait for a human (0 = forever), takeover budget, slowdown per takeover, wait-out after back-to-back challenges |
| `--show-state`, `--forget PATTERN` | review stored progress; drop cursors by signature substring (empty pattern drops all) |
| `--dry-run` | print the run plan and duration estimate, then stop without requesting anything |
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

Author publications land in the same JSONL (with Scholar's citation id in `extra.citation_id`); the profile header goes to `--profiles-out`:

```json
{"user_id":"kukA0LcAAAAJ","name":"Yoshua Bengio",
 "affiliation":"Professor of computer science, University of Montreal, Mila, IVADO, CIFAR",
 "organization":"University of Montreal","homepage":"https://yoshuabengio.org/",
 "verified_email":"Verified email at umontreal.ca",
 "interests":["Machine learning","deep learning","artificial intelligence"],
 "cited_by_total":1149112,"cited_by_recent":764217,"h_index":259,"h_index_recent":208,
 "i10_index":1106,"i10_index_recent":947,"fetched_at":"..."}
```

## About citation-graph expansion

`--follow-cites DEPTH` takes the records already collected, keeps the `--follow-breadth` most-cited of them, opens each one's "Cited by" listing, and repeats for the requested number of levels.

- Requests grow multiplicatively: one seed at depth 2 and breadth 5 is up to 31 listings, each still paged by `-p`. The worst-case count is printed before the run starts.
- Every cites id is crawled at most once per run, so repeated branches are skipped; each record records the level it came from in `extra.follow_depth`.
- Expanded listings inherit the year, language and sorting filters given on the command line, and `--resume` tracks each one by its own signature.
- Publications collected from an author profile can seed the expansion too.

## About the BibTeX export

`--bibtex` costs two extra page loads per record: Scholar's "Cite" popup, then the signed `scholar.bib` link inside it (the signature cannot be constructed locally). So:

- A 10-result page becomes 21 requests instead of 1, an order of magnitude slower, with a matching rise in challenge risk. Pair it with `-n` and export only what you will cite.
- Both loads are ordinary navigations in the visible window, so pacing and human takeover cover them. A background HTTP request is not an option: Scholar answers 429 to the same request issued outside the browser's navigation stack.
- Author-profile publications carry no Scholar `data-cid`, so it is resolved through their cluster listing first: three loads per record instead of two, announced once at the start.

## Rehearsing the human takeover

A real CAPTCHA cannot be summoned on demand, so the whole takeover path can be rehearsed against a locally generated page (**no request is sent**):

```sh
scholar-crawler --rehearse-handoff
```

The flow is the one a real challenge triggers: the page is detected as a challenge, the bell rings, the window comes to the front, the takeover notice prints, and the wait polls. Press the button on the page to stand in for solving it — the rehearsal then confirms the page reads as content again and reports how long the wait took, exiting 0. With nobody acting it fails at `--handoff-timeout` (exit 1), and with `--headless` it verifies the "no window, so refuse" path instead.

## Run summary and adaptive slowdown

Every run ends with a one-line summary — after a normal finish, a Ctrl+C, or a failure alike:

```
[run] 12 requests in 3.4 min (3.5/min), 1 takeover (captcha x1), 0 navigation retries, delay now 6.4-17.6s
```

The request count includes cite popups and BibTeX exports, takeovers are broken down by kind, and `delay now` is the rhythm after any backoff — noticeably above your starting values means this run was challenged and the address or the pacing needs to be more conservative. Runs shorter than 30 seconds report seconds instead of a rate, which would mostly measure start-up.

The slowdown has two stages: every takeover widens the delays by `--backoff-factor`, and a challenge that arrives with **no successful page in between** — meaning solving the first one did not restore trust — waits out `--challenge-cooldown` before resuming, doubling for a third consecutive challenge and so on. `--max-handoffs` still aborts the run outright.

## Getting challenged less

- Do not shrink the default delays; rhythm, not the User-Agent, is what gets a client blocked.
- Reuse one long-lived profile instead of deleting `.scholar-profile` between runs.
- Keep a single run to tens of pages; spread large collections over days.
- For large-scale metadata, prefer sources with real APIs (Semantic Scholar, OpenAlex, Crossref) and keep this tool for what only Scholar has.

## Development

```sh
python3 -m pytest -q     # 161 tests, fully offline
ruff check .             # same lint configuration as CI
```

Tests cover result parsing (citation-only cards, PDF side links, cited-by/version counts, bolded query terms mid-word, the page-two result count, zero-hit pages), author-profile parsing (header lines, the position-read summary table, publication rows, zero citations and missing years, the "show more" state), URL and filter assembly, id/URL parsing, JSONL dedup and CSV export, profile upserts, resume state, challenge detection (real headless Chromium DOM), the takeover wait including timeout, closed window and headless refusal, BibTeX link discovery (by href, not label) and `<pre>` extraction, `.bib` dedup, a takeover during export, grouping (first-author extraction, venue normalization, labels for all four dimensions, citation ranking and medians, hidden small groups, table alignment), real-page regression (all ten self-checks on a real result page, field completeness, profile stats and publication rows, cite popup to BibTeX, the sanitizing rules and a no-credentials scan of the fixtures), resume-state review and reset (signatures rendered back, timestamps, older files, substring removal, a capped target staying resumable), the run plan (page budget narrowed by `-n`, profiles counted in hundreds, the multiplicative cost of expansion and BibTeX, duration formats, `--dry-run` writing no files), the run summary (short and long duration formats, takeovers by kind), the back-to-back challenge wait and its off switch, the takeover rehearsal (detected as a challenge in a real DOM, cleared by the button, uncleared and undetected reporting, headless refusal), the offline digest (merge precedence, `extra` merging, shallowest level, combined filters, summary counts and ranking, file writing, flag validation), the self-check report (healthy page, zero-hit page, pinpointed missing fields, last page, output format), card-id resolution for profile records, citation-graph expansion (most-cited ordering, breadth cap, visited dedup, citation floor, level progression and early convergence), pagination and author batching, result-cap truncation, unknown profile layouts failing loudly, post-takeover slowdown, HTML dumps, and CLI argument assembly. GitHub Actions runs the same suite on Python 3.10 and 3.13.

## Compliance

Google Scholar's terms do not allow automated scraping, and collected metadata remains the publishers' copyright. Use this at personal research scale, respect the target site's terms and `robots.txt`, and do not resell or redistribute scraped results. The deliberately slow pacing and the human-in-the-loop design are what keep this from being a mass-abuse tool.

## Layout

```
scholar_crawler/
  urls.py       query and profile URLs, filters, id/URL parsing
  parser.py     result-page and profile HTML -> structured records
  challenge.py  challenge detection + human takeover wait
  browser.py    persistent-profile browser session
  crawler.py    crawl loop: pacing, takeover, grouping (first-author extraction, venue normalization, labels for all four dimensions, citation ranking and medians, hidden small groups, table alignment), real-page regression (all ten self-checks on a real result page, field completeness, profile stats and publication rows, cite popup to BibTeX, the sanitizing rules and a no-credentials scan of the fixtures), resume-state review and reset (signatures rendered back, timestamps, older files, substring removal, a capped target staying resumable), the run plan (page budget narrowed by `-n`, profiles counted in hundreds, the multiplicative cost of expansion and BibTeX, duration formats, `--dry-run` writing no files), the run summary (short and long duration formats, takeovers by kind), the back-to-back challenge wait and its off switch, the takeover rehearsal (detected as a challenge in a real DOM, cleared by the button, uncleared and undetected reporting, headless refusal), the offline digest (merge precedence, `extra` merging, shallowest level, combined filters, summary counts and ranking, file writing, flag validation), the self-check report (healthy page, zero-hit page, pinpointed missing fields, last page, output format), card-id resolution for profile records, citation-graph expansion (most-cited ordering, breadth cap, visited dedup, citation floor, level progression and early convergence), pagination and author batching, HTML dumps
  storage.py    JSONL/CSV writers, author profile records, the .bib file, resume state
  cli.py        command-line entry point
tests/          offline tests, including headless-Chromium detection tests
```

MIT licensed.
