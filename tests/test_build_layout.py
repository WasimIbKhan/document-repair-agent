import json

import pymupdf
import pytest

from toc_repair import LayoutDoc
from toc_repair.build_layout import build_layout

BODY = "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor."
OUTLINE = [[1, "My Book Title", 1], [2, "Getting Started", 1], [1, "1. Alpha", 2], [2, "1.1 Beta", 3]]


def _write(page, y, text, size, bold=False):
    page.insert_text((72, y), text, fontsize=size, fontname="hebo" if bold else "helv")


def make_pdf(path):
    doc = pymupdf.open()
    p = doc.new_page()
    _write(p, 100, "My Book Title", 24, bold=True)
    _write(p, 150, "Getting Started", 16, bold=True)
    _write(p, 190, BODY, 11)
    _write(p, 210, BODY, 11)
    for heading in ("1. Alpha", "1.1 Beta"):
        p = doc.new_page()
        _write(p, 100, heading, 16, bold=True)
        _write(p, 140, BODY, 11)
        _write(p, 160, BODY, 11)
    doc.set_toc(OUTLINE)
    doc.save(path)
    doc.close()


def make_content_list(pdf_path, per_mille=True):
    doc = pymupdf.open(pdf_path)
    items = []
    for i, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height
        for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
            text = text.strip()
            bbox = [x0, y0, x1, y1]
            if per_mille:
                bbox = [round(x0 * 1000 / w), round(y0 * 1000 / h), round(x1 * 1000 / w), round(y1 * 1000 / h)]
            item = {"type": "text", "text": text, "bbox": bbox, "page_idx": i}
            if text == "My Book Title":
                item.update(text="# " + text, text_level=1)
            elif text == "1.1 Beta":
                item.update(text=text + "<sub>x</sub>", text_level=2)
            elif text in ("Getting Started", "1. Alpha"):
                item["text_level"] = 2
            items.append(item)
    doc.close()
    return items


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "Sample Book.pdf"
    make_pdf(path)
    return path


def _build(tmp_path, pdf, per_mille):
    cl = tmp_path / "content_list.json"
    cl.write_text(json.dumps(make_content_list(pdf, per_mille)), encoding="utf-8")
    return build_layout(pdf, cl)


def test_per_mille_layout(tmp_path, pdf):
    layout = _build(tmp_path, pdf, per_mille=True)
    assert layout.source["bbox_scale_detected"] == "per_mille"
    assert layout.doc_id == "sample-book"
    assert layout.page_count == 3

    title = next(b for b in layout.blocks if b.text == "My Book Title")
    assert title.font_size == pytest.approx(24, abs=0.5)
    assert title.bold is True
    assert title.raw_text == "# My Book Title"
    assert title.parser_level == 1

    beta = next(b for b in layout.blocks if b.text.startswith("1.1 Beta"))
    assert "<" not in beta.text
    assert "<sub>" in beta.raw_text

    body = next(b for b in layout.blocks if b.text.startswith("Lorem"))
    assert body.font_size == pytest.approx(11, abs=0.5)
    assert body.bold is False

    ids = [b.id for b in layout.blocks]
    assert len(ids) == len(set(ids)) and ids == sorted(ids)
    assert len(layout.blocks_on_page(0)) >= 3
    assert layout.block_by_id(title.id) is title
    hist = layout.font_size_histogram()
    assert hist[24.0] == len("My Book Title") and hist[11.0] > hist[16.0]

    assert [[e.level, e.title, e.page + 1] for e in layout.embedded_outline] == OUTLINE
    assert layout.source["median_text_overlap"] > 90

    again = LayoutDoc.model_validate_json(layout.model_dump_json())
    assert again == layout


def test_points_scale_detected(tmp_path, pdf):
    layout = _build(tmp_path, pdf, per_mille=False)
    assert layout.source["bbox_scale_detected"] == "points"
    title = next(b for b in layout.blocks if b.text == "My Book Title")
    assert title.font_size == pytest.approx(24, abs=0.5)
