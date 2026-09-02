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

## Options

| Option | Meaning |
| --- | --- |
| `-q/--query`, `--queries-file` | keyword search, repeatable; file holds one query per line |
| `--cites`, `--cluster` | crawl citing works / all versions of one work; accepts a numeric id or a `cited_by_url`/`versions_url`, repeatable |
| `--author` | crawl an author's publication list; accepts a 12-character user id or a profile URL, repeatable; `--sort-by-date` orders by year |
| `-p/--pages`, `-n/--max-results` | pages per entry point / hard result cap (last page truncated exactly). Search pages hold 10 results, profile pages 100 publications |
| `--start`, `--resume` | first offset; continue from the saved cursor |
| `--year-from/--year-to`, `--sort-by-date`, `--review-only` | year range, date order, reviews only |
| `--no-citations`, `--no-patents` | exclude citation-only records / patents |
| `--lang`, `--host` | interface language (`hl`); mirror such as `https://scholar.google.de` |
| `-o/--out`, `--csv`, `--state` | JSONL output, CSV export, resume state |
| `--bibtex` | also export BibTeX to a `.bib` file; deduplicated by citation key, with `extra.bibtex_key` recorded on each record |
| `--profiles-out`, `--dump-html` | author profile headers (one record per author, re-crawls replace it), raw HTML of every fetched page |
| `--profile`, `--channel`, `--locale`, `--timezone`, `--proxy` | browser profile and environment |
| `--min-delay/--max-delay`, `--cooldown-every/--cooldown-seconds` | request rhythm |
| `--handoff-timeout`, `--max-handoffs`, `--backoff-factor` | how long to wait for a human (0 = forever), takeover budget, slowdown per takeover |
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

## About the BibTeX export

`--bibtex` costs two extra page loads per record: Scholar's "Cite" popup, then the signed `scholar.bib` link inside it (the signature cannot be constructed locally). So:

- A 10-result page becomes 21 requests instead of 1, an order of magnitude slower, with a matching rise in challenge risk. Pair it with `-n` and export only what you will cite.
- Both loads are ordinary navigations in the visible window, so pacing and human takeover cover them. A background HTTP request is not an option: Scholar answers 429 to the same request issued outside the browser's navigation stack.
- Author-profile publications carry no Scholar `data-cid`, so they cannot use this export path and are skipped with a single notice.

## Getting challenged less

- Do not shrink the default delays; rhythm, not the User-Agent, is what gets a client blocked.
- Reuse one long-lived profile instead of deleting `.scholar-profile` between runs.
- Keep a single run to tens of pages; spread large collections over days.
- For large-scale metadata, prefer sources with real APIs (Semantic Scholar, OpenAlex, Crossref) and keep this tool for what only Scholar has.

## Development

```sh
python3 -m pytest -q     # 79 tests, fully offline
ruff check .             # same lint configuration as CI
```

Tests cover result parsing (citation-only cards, PDF side links, cited-by/version counts, bolded query terms mid-word, the page-two result count, zero-hit pages), author-profile parsing (header lines, the position-read summary table, publication rows, zero citations and missing years, the "show more" state), URL and filter assembly, id/URL parsing, JSONL dedup and CSV export, profile upserts, resume state, challenge detection (real headless Chromium DOM), the takeover wait including timeout, closed window and headless refusal, BibTeX link discovery (by href, not label) and `<pre>` extraction, `.bib` dedup, a takeover during export, pagination and author batching, result-cap truncation, unknown profile layouts failing loudly, post-takeover slowdown, HTML dumps, and CLI argument assembly. GitHub Actions runs the same suite on Python 3.10 and 3.13.

## Compliance

Google Scholar's terms do not allow automated scraping, and collected metadata remains the publishers' copyright. Use this at personal research scale, respect the target site's terms and `robots.txt`, and do not resell or redistribute scraped results. The deliberately slow pacing and the human-in-the-loop design are what keep this from being a mass-abuse tool.

## Layout

```
scholar_crawler/
  urls.py       query and profile URLs, filters, id/URL parsing
  parser.py     result-page and profile HTML -> structured records
  challenge.py  challenge detection + human takeover wait
  browser.py    persistent-profile browser session
  crawler.py    crawl loop: pacing, takeover, pagination and author batching, HTML dumps
  storage.py    JSONL/CSV writers, author profile records, the .bib file, resume state
  cli.py        command-line entry point
tests/          offline tests, including headless-Chromium detection tests
```

MIT licensed.
