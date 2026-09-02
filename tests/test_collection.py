"""A folder as one collection, and the bookkeeping between two merges.

Keeping a literature collection current is the part nobody can do from memory: which files
belong to it, what arrived since last time, and which counts moved. These tests pin that
bookkeeping, including the trap of reading last run's merge back in as if it were new data.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scholar_crawler.collection import (  # noqa: E402
    Delta,
    Moved,
    collection_files,
    compare,
    render_delta,
)
from scholar_crawler.digest import main  # noqa: E402


def _record(key: str, citations: int | None = 10, title: str | None = None) -> dict[str, object]:
    return {
        "cluster_id": key,
        "title": title or f"Work {key}",
        "cited_by_count": citations,
        "year": 2021,
        "query": "graph attention networks",
    }


def _write(path: Path, records: list[dict[str, object]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_a_collection_reads_every_result_file_in_name_order(tmp_path: Path) -> None:
    _write(tmp_path / "b.jsonl", [_record("b")])
    _write(tmp_path / "a.jsonl", [_record("a")])
    (tmp_path / "notes.txt").write_text("not a result file", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    assert [path.name for path in collection_files(tmp_path)] == ["a.jsonl", "b.jsonl"]


def test_the_file_this_run_writes_is_not_one_of_its_inputs(tmp_path: Path) -> None:
    # Reading last run's merge back in makes a collection look complete while it stands still.
    _write(tmp_path / "session1.jsonl", [_record("a")])
    merged = _write(tmp_path / "merged.jsonl", [_record("a")])
    assert [path.name for path in collection_files(tmp_path, exclude=[merged])] == ["session1.jsonl"]


def test_a_path_that_is_not_a_directory_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "one.jsonl", [_record("a")])
    with pytest.raises(NotADirectoryError, match="is not a directory"):
        collection_files(path)


def test_comparing_two_merges_names_what_arrived_and_what_moved() -> None:
    before = [_record("a", 100), _record("b", 50), _record("c", 10)]
    after = [_record("a", 140), _record("b", 50), _record("d", 4)]
    delta = compare(before, after)
    assert delta.added == ["Work d"]
    assert delta.gone == ["Work c"]
    assert [(item.label, item.change) for item in delta.moved] == [("Work a", 40)]
    assert delta.same == 1
    assert (delta.before_total, delta.after_total) == (3, 3)
    assert delta.citations_gained == 40
    assert not delta.quiet()


def test_a_count_that_falls_is_reported_as_a_fall() -> None:
    delta = compare([_record("a", 150)], [_record("a", 118)])
    assert delta.moved[0].change == -32
    assert delta.citations_gained == -32
    assert "-32" in delta.moved[0].describe()


def test_the_biggest_movement_comes_first_whichever_way_it_went() -> None:
    before = [_record("a", 100), _record("b", 100), _record("c", 100)]
    after = [_record("a", 105), _record("b", 40), _record("c", 120)]
    delta = compare(before, after)
    assert [item.label for item in delta.moved] == ["Work b", "Work c", "Work a"]


def test_a_record_without_a_count_on_either_side_is_not_a_movement() -> None:
    delta = compare([_record("a", None), _record("b", 10)], [_record("a", 30), _record("b", None)])
    assert delta.moved == []
    assert delta.same == 2


def test_an_unchanged_collection_says_so_in_one_line() -> None:
    records = [_record("a", 10), _record("b", 20)]
    delta = compare(records, list(records))
    assert delta.quiet()
    assert render_delta(delta) == ["nothing changed: the same 2 works, same counts"]


def test_the_report_names_the_file_it_compared_against(tmp_path: Path) -> None:
    delta = compare([_record("a", 10)], [_record("a", 12)])
    lines = render_delta(delta, since=tmp_path / "merged.jsonl")
    assert f"since {tmp_path / 'merged.jsonl'}" in lines[0]


def test_a_long_list_of_new_works_is_cut_with_a_count(tmp_path: Path) -> None:
    after = [_record(f"n{index}") for index in range(9)]
    lines = render_delta(compare([], after), top=3)
    assert "  ... and 6 more" in lines


def test_the_report_explains_what_a_missing_work_means() -> None:
    lines = render_delta(compare([_record("a")], []))
    assert any("its file was removed or a filter now excludes it" in line for line in lines)
    assert not any("Scholar dropped" in line and "not because" not in line for line in lines)


def test_a_movement_reads_as_a_signed_change_and_a_new_total() -> None:
    assert Moved(label="A paper", before=1000, after=1200).describe().startswith("  +   200  now")


def test_an_empty_delta_is_still_a_delta() -> None:
    delta = Delta(added=[], gone=[], moved=[], same=0, before_total=0, after_total=0)
    assert delta.quiet()
    assert delta.citations_gained == 0


def test_a_digest_over_a_folder_skips_the_merge_it_is_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "session1.jsonl", [_record("a", 100), _record("b", 50)])
    _write(tmp_path / "session2.jsonl", [_record("a", 140), _record("c", 7)])
    merged = tmp_path / "merged.jsonl"
    _write(merged, [_record("a", 100), _record("b", 50)])
    code = main(["--collection", str(tmp_path), "--since", str(merged), "-o", str(merged)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "4 records from 2 file(s)" in printed, "the previous merge is not an input"
    assert "2 works since" in printed
    assert "1 new, 0 no longer here, 1 with a new citation count" in printed
    assert "[out] 3 records ->" in printed


def test_a_folder_with_nothing_left_to_read_says_which_file_it_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    merged = _write(tmp_path / "merged.jsonl", [_record("a")])
    code = main(["--collection", str(tmp_path), "-o", str(merged)])
    assert code == 1
    assert f"holds no .jsonl files to read besides {merged}" in capsys.readouterr().out


def test_neither_files_nor_a_folder_is_a_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 1
    assert "give some JSONL files to read, or a folder with --collection DIR" in capsys.readouterr().out


def test_a_missing_earlier_merge_is_reported_as_such(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "one.jsonl", [_record("a")])
    code = main([str(path), "--since", str(tmp_path / "absent.jsonl")])
    assert code == 1
    assert "no earlier merge to compare against" in capsys.readouterr().out


def test_a_folder_and_named_files_can_be_read_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "folder" / "session1.jsonl", [_record("a")])
    extra = _write(tmp_path / "elsewhere.jsonl", [_record("z")])
    assert main(["--collection", str(tmp_path / "folder"), str(extra)]) == 0
    assert "2 records from 2 file(s)" in capsys.readouterr().out
