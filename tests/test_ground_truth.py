import json

import pytest

from toc_repair import Block, LayoutDoc, Page
from toc_repair.ground_truth import (
    GroundTruthError, build_report, init_case, is_template, parse_ground_truth_text,
    resolve_against_layout, run_case, write_ground_truth_json,
)

TEXT = """# a comment
Introduction @ 1

1. Alpha @ 2
  1.1 Beta @ 2
\t1.2 Gamma @ 3
    Deep @ 3
"""

PAGES = {
    0: ["Introduction", "Lorem ipsum dolor sit amet body text here."],
    1: ["1. Alpha", "1.1 Beta", "Lorem ipsum dolor sit amet body text here."],
    2: ["1.2 Gamma", "Lorem ipsum dolor sit amet body text here."],
    3: ["Epilogue and Notes", "Lorem ipsum dolor sit amet body text here."],
}


def make_layout() -> LayoutDoc:
    blocks = []
    for p, texts in PAGES.items():
        for n, t in enumerate(texts):
            blocks.append(Block(id=f"p{p:04d}-b{n:03d}", page=p, order=n, type="text", text=t,
                                raw_text=t, bbox=(0, n * 20, 100, n * 20 + 10), n_chars=len(t)))
    return LayoutDoc(doc_id="synthetic", source={"pdf": "synthetic.pdf"},
                     pages=[Page(index=i, width=100, height=100) for i in PAGES], blocks=blocks)


def test_parse_levels_pages_and_lines():
    entries = parse_ground_truth_text(TEXT)
    assert [(e["title"], e["level"], e["page"]) for e in entries] == [
        ("Introduction", 1, 0), ("1. Alpha", 1, 1), ("1.1 Beta", 2, 1), ("1.2 Gamma", 2, 2), ("Deep", 3, 2)]
    assert [e["line"] for e in entries] == [2, 4, 5, 6, 7]


@pytest.mark.parametrize("text,line", [
    ("Intro @ 1\nNo page here\n", 2),
    ("Intro @ 1\n\nIntro @ one\n", 3),
    ("Intro @ 1\n   Odd @ 2\n", 2),
    ("Intro @ 1\n    Jump @ 2\n", 2),
    ("  Starts nested @ 1\n", 1),
    ("@ 3\n", 1),
])
def test_hard_errors_report_line(text, line):
    with pytest.raises(GroundTruthError) as exc:
        parse_ground_truth_text(text)
    assert exc.value.line == line and f"line {line}:" in str(exc.value)


def test_resolve_exact_fallback_and_unmatched():
    layout = make_layout()
    entries = parse_ground_truth_text("Introduction @ 1\n1. Alpha @ 2\n  1.1 Beta @ 2\n"
                                      "  1.2 Gamma @ 2\nEpilogue @ 4\nEpilog and Nots @ 3\nMissing Chapter @ 4\n")
    resolved = resolve_against_layout(entries, layout)
    by_title = {r["title"]: r for r in resolved}
    assert by_title["Introduction"]["block_id"] == "p0000-b000"
    assert by_title["1. Alpha"]["block_id"] == "p0001-b000"
    assert by_title["1.1 Beta"]["block_id"] == "p0001-b001"
    assert by_title["1.2 Gamma"]["block_id"] == "p0002-b000"  # written @2, found on page 3
    assert by_title["Epilogue"]["block_id"] == "p0003-b000"  # short title, partial match
    assert by_title["Epilog and Nots"]["block_id"] == "p0003-b000"  # typo + page slip
    assert by_title["Missing Chapter"]["block_id"] is None
    assert by_title["Missing Chapter"]["match_score"] is None
    assert len(by_title["Missing Chapter"]["candidates"]) == 2

    report = build_report(resolved)
    unmatched = [l for l in report if "unmatched" in l]
    assert len(unmatched) == 1 and "Missing Chapter" in unmatched[0] and "Epilogue and Notes" in unmatched[0]
    assert any("page decreases" in l for l in report)


def test_page_out_of_range_is_hard():
    with pytest.raises(GroundTruthError) as exc:
        resolve_against_layout(parse_ground_truth_text("Intro @ 1\nFar @ 99\n"), make_layout())
    assert exc.value.line == 2


def test_json_roundtrip_and_cli(tmp_path, capsys):
    layout = make_layout()
    (tmp_path / "layout.json").write_text(layout.model_dump_json(), encoding="utf-8")
    assert init_case(tmp_path) and not init_case(tmp_path)
    tmpl = (tmp_path / "ground_truth_toc.txt").read_text(encoding="utf-8")
    assert is_template(tmpl) and "synthetic.pdf" in tmpl and "pages: 4" in tmpl

    (tmp_path / "ground_truth_toc.txt").write_text(tmpl + "Introduction @ 1\n1. Alpha @ 2\n  Nope @ 2\n", encoding="utf-8")
    assert run_case(tmp_path) == 0
    out = capsys.readouterr().out
    assert "entries=3 matched=2 unmatched=1 warnings=1" in out

    data = json.loads((tmp_path / "ground_truth_toc.json").read_text(encoding="utf-8"))
    assert data["doc_id"] == "synthetic" and data["source"] == "hand-written"
    assert data["entries"][0] == {"title": "Introduction", "level": 1, "page": 0, "block_id": "p0000-b000", "match_score": 100.0}
    assert data["entries"][2]["block_id"] is None and set(data["entries"][2]) == {"title", "level", "page", "block_id", "match_score"}

    resolved = resolve_against_layout(parse_ground_truth_text("Introduction @ 1\n"), layout)
    write_ground_truth_json(tmp_path / "again.json", layout, resolved)
    assert json.loads((tmp_path / "again.json").read_text(encoding="utf-8"))["entries"] == data["entries"][:1]

    (tmp_path / "ground_truth_toc.txt").write_text("Bad line\n", encoding="utf-8")
    assert run_case(tmp_path) == 1
