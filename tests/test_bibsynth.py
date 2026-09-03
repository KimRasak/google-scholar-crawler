"""Building BibTeX offline from stored records: keys, entry types, escaping, export."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.bibsynth import (  # noqa: E402
    ascii_slug,
    authors_field,
    entry_type,
    make_key,
    surname,
    synthesize_entry,
    venue_field,
    write_bibtex,
)
from scholar_crawler.digest import main  # noqa: E402
from scholar_crawler.parser import bibtex_key  # noqa: E402


def _record(**overrides: object) -> dict[str, object]:
    record: dict[str, object] = {
        "cluster_id": "c1",
        "title": "Graph attention networks",
        "link": "https://arxiv.org/abs/1710.10903",
        "authors": "P Veličković, G Cucurull, A Casanova…",
        "byline": "P Veličković, G Cucurull, A Casanova… - arXiv preprint arXiv …, 2017 - arxiv.org",
        "venue": "arXiv preprint arXiv …",
        "year": 2017,
        "cited_by_count": 41135,
        "citation_only": False,
        "extra": {},
    }
    record.update(overrides)
    return record


def test_accents_and_undecomposable_letters_reduce_to_ascii() -> None:
    assert ascii_slug("Veličković") == "velickovic"
    assert ascii_slug("Łukasz") == "lukasz"
    assert ascii_slug("Søren") == "soren"
    assert ascii_slug("Weiß") == "weiss"
    assert ascii_slug("!!!") == ""


def test_the_surname_is_the_last_word_of_the_first_name() -> None:
    assert surname("P Veličković, G Cucurull") == "Veličković"
    assert surname("Y LeCun, Y Bengio, G Hinton - nature, 2015 - nature.com") == "LeCun"
    assert surname("A Casanova…") == "Casanova"
    assert surname("") == ""


def test_a_truncated_author_list_is_marked_with_others() -> None:
    value, truncated = authors_field(_record())
    assert value == "P Veličković and G Cucurull and A Casanova and others"
    assert truncated is True

    value, truncated = authors_field(_record(authors="S Brody, U Alon"))
    assert (value, truncated) == ("S Brody and U Alon", False)

    value, truncated = authors_field(_record(authors=None, byline="Z Liu - Springer, 2022 - x.com"))
    assert (value, truncated) == ("Z Liu", False)


def test_the_venue_keeps_volumes_but_drops_truncation_marks() -> None:
    assert venue_field(_record()) == "arXiv preprint"
    assert venue_field(_record(venue="nature 521 (7553), 436-444, 2015")) == (
        "nature 521 (7553), 436-444, 2015"
    )
    assert venue_field(_record(venue=None)) == ""


def test_entry_types_follow_the_venue() -> None:
    assert entry_type(_record()) == "article"
    assert entry_type(_record(venue="Proceedings of the 40th ICML")) == "inproceedings"
    assert entry_type(_record(venue="IEEE Workshop on Vision")) == "inproceedings"
    assert entry_type(_record(venue=None)) == "misc"


def test_keys_are_generated_reused_and_kept_unique() -> None:
    used: set[str] = set()
    assert make_key(_record(), used) == "velickovic2017graph"
    assert make_key(_record(), used) == "velickovic2017grapha"  # same work seen twice
    assert make_key(_record(extra={"bibtex_key": "scholars2017key"}), used) == "scholars2017key"
    assert make_key(_record(authors="", byline="", title="The end of theory", year=None), used) == (
        "anonend"
    )


def test_an_entry_reads_back_with_the_crawler_parser() -> None:
    entry = synthesize_entry(_record(), "velickovic2017graph")
    assert entry.startswith("@article{velickovic2017graph,\n")
    assert bibtex_key(entry) == "velickovic2017graph"
    assert "title = {{Graph attention networks}}" in entry  # capitalization protected
    assert "journal = {arXiv preprint}" in entry
    assert "url = {https://arxiv.org/abs/1710.10903}" in entry
    assert "note = {cited by 41135 on Google Scholar}" in entry


def test_latex_specials_and_braces_are_neutralized() -> None:
    entry = synthesize_entry(
        _record(title="Cost & benefit of 50% {sparsity}", venue="A_Journal", link=None), "k"
    )
    assert r"Cost \& benefit of 50\% (sparsity)" in entry
    assert r"journal = {A\_Journal}" in entry
    assert "url" not in entry


def test_the_two_silent_latex_specials_are_escaped_too() -> None:
    # ^ stops a LaTeX run outside math mode, and ~ quietly becomes a non-breaking space, so a
    # title like "O(n^2) scaling ~ a note" either fails to compile or prints wrong.
    entry = synthesize_entry(_record(title="O(n^2) scaling ~ 50 GB", link=None), "k")
    assert r"O(n\textasciicircum{}2) scaling \textasciitilde{} 50 GB" in entry


def test_a_conference_paper_uses_booktitle() -> None:
    entry = synthesize_entry(_record(venue="Proceedings of NeurIPS"), "k")
    assert entry.startswith("@inproceedings{")
    assert "booktitle = {Proceedings of NeurIPS}" in entry
    assert "journal" not in entry


def test_writing_a_bibliography_reports_what_it_did(tmp_path: Path) -> None:
    records = [
        _record(),
        _record(cluster_id="c2", extra={"bibtex_key": "brody2021how"}, authors="S Brody, U Alon"),
        _record(cluster_id="c3", title="  "),
    ]
    report = write_bibtex(records, tmp_path / "refs.bib")
    assert (report.written, report.reused_keys, report.skipped) == (2, 1, 1)
    assert report.truncated_authors == 1
    assert "1 keys from the crawl" in report.describe()
    assert "1 skipped without a title" in report.describe()

    written = (tmp_path / "refs.bib").read_text(encoding="utf-8")
    assert written.count("@") == 2
    assert "brody2021how" in written


def test_digest_writes_a_bibliography(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "in.jsonl"
    source.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    target = tmp_path / "refs.bib"
    assert main([str(source), "--bibtex", str(target), "--quiet"]) == 0
    assert "1 entries ->" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8").startswith("@article{velickovic2017graph,")


def test_quiet_is_satisfied_by_a_bibliography_alone(tmp_path: Path) -> None:
    source = tmp_path / "in.jsonl"
    source.write_text(json.dumps(_record()) + "\n", encoding="utf-8")
    assert main([str(source), "--bibtex", str(tmp_path / "refs.bib"), "--quiet"]) == 0
