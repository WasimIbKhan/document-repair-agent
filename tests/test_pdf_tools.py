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
    assert header["text"] == HEADER and header.get("running") is True


def test_search_ignores_diacritics(pdf):
    found = pdf.search("tafsir")
    assert ("Sources of Tafsīr", 4) in [(m["text"], m["page"]) for m in found["matches"]]
    assert pdf.search("body text", max_results=2)["note"].startswith("showing the first 2 of")


def test_find_heading_joins_split_lines(pdf):
    top = pdf.find_heading("Part One VISION", near_page=3)["candidates"][0]
    assert top["ids"] == ids_of(pdf, 3, "Part One") + ids_of(pdf, 3, "VISION")
    assert top["score"] == 100 and top["size"] == 21.0


def test_find_heading_prefers_heading_and_skips_running(pdf):
    top = pdf.find_heading("Sources of Tafsīr", near_page=4)["candidates"][0]
    assert top["ids"] == ids_of(pdf, 4, "Sources of Tafsīr") and top["bold"] and top["size"] == 14.0
    running = {ln["id"] for p in range(2, 7) for ln in pdf.page_lines(p) if ln.get("running")}
    cands = pdf.find_heading(HEADER, near_page=4)["candidates"]
    assert not running & {i for c in cands for i in c["ids"]}


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
                     "pdf_page_for_printed", "pdf_render_page"}
    with pytest.raises(ToolError, match="page 400 is out of range"):
        asyncio.run(server.call_tool("pdf_page_lines", {"doc_id": "synthetic", "page": 400}))
    result = asyncio.run(server.call_tool("pdf_page_for_printed", {"doc_id": "synthetic", "printed_page": 3}))
    assert '"pdf_page": 4' in result.content[0].text
