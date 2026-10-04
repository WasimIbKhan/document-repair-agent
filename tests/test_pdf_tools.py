import asyncio
import re

import pymupdf
import pytest

from toc_repair import pdf_tools
from toc_repair.pdf_tools import PdfDoc

HEADER = "A Synthetic Book"
BODY = ["Body text line one of the page.", "Body text line two continues here.", "Body text line three ends it."]


def build_pdf(path):
    doc = pymupdf.open()
    contents = doc.new_page(width=400, height=600)
    for i, line in enumerate(["Contents", "Chapter One 1", "Part Two 3", "Sources of Tafsir 4",
                              "Chapter Four 5", "Notes 5"]):
        contents.insert_text((40, 80 + 25 * i), line, fontsize=11)
    for folio in range(1, 6):
        page = doc.new_page(width=400, height=600)
        page.insert_text((40, 30), HEADER, fontsize=8)
        y = 100
        if folio == 2:
            page.insert_text((40, 100), "Part One", fontsize=21)
            page.insert_text((40, 130), "VISION", fontsize=21)
            y = 170
        if folio == 3:
            page.insert_htmlbox(pymupdf.Rect(40, 85, 380, 115), '<b style="font-size:14px">Sources of Tafsīr</b>')
            y = 140
        for i, line in enumerate(BODY):
            page.insert_text((40, y + 16 * i), line, fontsize=11)
        page.insert_text((195, 585), str(folio), fontsize=9)
    doc.save(path)


def make_pdf(path, pages):
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page(width=400, height=600)
        for x, y, text, size in lines:
            page.insert_text((x, y), text, fontsize=size)
    doc.save(path)
    return PdfDoc(path)


@pytest.fixture()
def pdf(tmp_path):
    path = tmp_path / "synthetic.pdf"
    build_pdf(path)
    return PdfDoc(path)


def ids_of(pdf, page, text):
    return [ln["id"] for ln in pdf.page_lines(page) if ln["text"] == text]


def test_line_ids_are_formatted_and_stable(pdf, tmp_path):
    lines = pdf.page_lines(3)
    assert all(re.fullmatch(r"p003-l\d{2}", ln["id"]) for ln in lines)
    assert [ln["id"] for ln in lines] == [f"p003-l{i:02d}" for i in range(len(lines))]
    again = tmp_path / "again.pdf"
    build_pdf(again)
    assert PdfDoc(again).page_lines(3) == lines
    detailed = pdf.page_lines(3, "detailed")[1]
    assert {"bbox", "font", "y"} <= detailed.keys()


def test_page_map_and_printed_lookup(pdf):
    pm = pdf.page_map()
    assert pm["offset"] == 1 and pm["agreement"] == 1.0 and pm["numbered_pages"] == 5
    assert pdf.pdf_page_for(3) == 4


def test_summary_finds_contents_and_running_lines(pdf):
    s = pdf.summary()
    assert s["pages"] == 6 and s["body_font_size"] == 11.0
    assert 1 in [c["page"] for c in s["contents_pages"]]
    assert {"text": "a synthetic book", "pages": 5} in s["running_lines"]
    header = pdf.page_lines(2)[0]
    assert header["text"] == HEADER and header["margin"] is True and header["repeats_on_pages"] == 5


def test_search_ignores_diacritics(pdf):
    found = pdf.search("tafsir")
    assert ("Sources of Tafsīr", 4) in [(m["text"], m["page"]) for m in found["matches"]]
    assert pdf.search("body text", max_results=2)["note"].startswith("showing the first 2 of")


def test_search_puts_body_matches_before_margin_matches(tmp_path):
    header = (40, 30, "Synthetic Book", 8)
    doc = make_pdf(tmp_path / "s.pdf", [[header], [header], [header, (40, 300, "The Synthetic Book reviewed", 11)]])
    found = doc.search("synthetic book")
    assert found["total"] == 4 and found["margin_matches"] == 3
    first, *rest = found["matches"]
    assert first["page"] == 3 and "margin" not in first
    assert all(m["margin"] is True and m["repeats_on_pages"] == 3 for m in rest)


def test_find_heading_joins_split_lines(pdf):
    top = pdf.find_heading("Part One VISION", near_page=3)["candidates"][0]
    assert top["ids"] == ids_of(pdf, 3, "Part One") + ids_of(pdf, 3, "VISION")
    assert top["score"] == 100 and top["size"] == 21.0


def test_find_heading_joins_three_lines(tmp_path):
    doc = make_pdf(tmp_path / "h.pdf", [[(40, 100, "The Book", 21), (40, 130, "of Many", 21), (40, 160, "Lines", 21),
                                         *[(40, 200 + 16 * i, line, 11) for i, line in enumerate(BODY)]]])
    top = doc.find_heading("The Book of Many Lines", near_page=1)["candidates"][0]
    assert top["ids"] == ["p001-l00", "p001-l01", "p001-l02"] and top["score"] == 100


def test_find_headings_returns_compact_rows(pdf):
    rows = pdf.find_headings([{"title": "Part One VISION", "near_page": 3},
                              {"title": "Sources of Tafsīr", "near_page": 4}])
    assert [r["title"] for r in rows] == ["Part One VISION", "Sources of Tafsīr"]
    assert rows[0]["best"]["ids"] == ids_of(pdf, 3, "Part One") + ids_of(pdf, 3, "VISION")
    assert rows[1]["best"]["ids"] == ids_of(pdf, 4, "Sources of Tafsīr") and rows[1]["best"]["bold"]
    for r in rows:
        assert set(r) == {"title", "near_page", "best", "runner_up"}
        assert r["runner_up"]["score"] <= r["best"]["score"]
        assert set(r["runner_up"]) <= {"ids", "page", "score", "size", "margin"}
        assert not any(k.startswith("context") for k in r["best"])
    with pytest.raises(ValueError, match="101 items is over the limit of 100"):
        pdf.find_headings([{"title": "Notes", "near_page": 2}] * 101)


def test_contents_view_pairs_numbers_wraps_titles_and_ranks_indents(tmp_path):
    doc = make_pdf(tmp_path / "c.pdf", [[
        (150, 60, "Contents", 14),
        (40, 100, "Part One", 11), (350, 100, "1", 11),
        (56, 120, "First Chapter ..... 3", 11),
        (56, 140, "A Very Long Chapter Title That Keeps On Going Right", 11),
        (56, 154, "Across Two Lines", 11), (350, 154, "7", 11),
        (40, 180, "Part Two", 11), (350, 180, "9", 11),
        (195, 585, "5", 9),
    ]])
    view = doc.contents_view(1)
    rows = [(r["title"], r["printed_page"], r["indent"], len(r["ids"])) for r in view["rows"]]
    assert rows == [("Part One", 1, 1, 2), ("First Chapter", 3, 2, 1),
                    ("A Very Long Chapter Title That Keeps On Going Right Across Two Lines", 7, 2, 3),
                    ("Part Two", 9, 1, 2)]
    assert view["indent_x0"] == [40, 56]


def test_find_heading_ranks_heading_first_and_flags_header(pdf):
    top = pdf.find_heading("Sources of Tafsīr", near_page=4)["candidates"][0]
    assert top["ids"] == ids_of(pdf, 4, "Sources of Tafsīr") and top["bold"] and top["size"] == 14.0
    assert "margin" not in top
    header = pdf.find_heading(HEADER, near_page=4)["candidates"][0]
    assert header["margin"] is True and header["repeats_on_pages"] == 5


def test_out_of_range_page_is_actionable(pdf):
    with pytest.raises(ValueError, match="page 400 is out of range; this PDF has 6 pages"):
        pdf.page_lines(400)


def test_render_page_returns_png(pdf):
    assert pdf.render_page(1).startswith(b"\x89PNG")


def test_server_registers_tools_and_reports_errors(pdf, monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError

    from toc_repair.mcp_server.server import server

    monkeypatch.setitem(pdf_tools._DOCS, "synthetic", pdf)
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"pdf_list_cases", "pdf_summary", "pdf_page_lines", "pdf_search", "pdf_find_heading",
                     "pdf_find_headings", "pdf_contents_view", "pdf_page_for_printed", "pdf_render_page"}
    result = asyncio.run(server.call_tool("pdf_find_headings", {
        "doc_id": "synthetic", "items": [{"title": "Part One VISION", "near_page": 3}]}))
    assert '"runner_up"' in result.content[0].text
    with pytest.raises(ToolError, match="page 400 is out of range"):
        asyncio.run(server.call_tool("pdf_page_lines", {"doc_id": "synthetic", "page": 400}))
    result = asyncio.run(server.call_tool("pdf_page_for_printed", {"doc_id": "synthetic", "printed_page": 3}))
    assert '"pdf_page": 4' in result.content[0].text
