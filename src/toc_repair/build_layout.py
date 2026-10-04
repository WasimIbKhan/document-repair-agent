import argparse
import hashlib
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

import pymupdf
from rapidfuzz import fuzz

from .schema import Block, LayoutDoc, OutlineEntry, Page, to_block_type

PAD = 2.0
SCALE_SAMPLE = 30
_HEADING_PREFIX = re.compile(r"^\s*(?:#+\s*)+")
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(raw: str) -> str:
    return _WS.sub(" ", _HTML_TAG.sub(" ", _HEADING_PREFIX.sub("", raw))).strip()


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "doc"


def item_text(item: dict) -> str:
    text = item.get("text")
    if text is None and isinstance(item.get("image_caption"), list):
        text = " ".join(item["image_caption"])
    return text or ""


class _Spans:
    def __init__(self, doc):
        self.doc = doc
        self.cache = {}

    def on_page(self, index):
        if index not in self.cache:
            spans = []
            for blk in self.doc[index].get_text("dict")["blocks"]:
                for line in blk.get("lines", []):
                    for s in line["spans"]:
                        x0, y0, x1, y1 = s["bbox"]
                        spans.append({
                            "cx": (x0 + x1) / 2, "cy": (y0 + y1) / 2,
                            "size": s["size"], "text": s["text"],
                            "bold": bool(s["flags"] & 16) or "bold" in s["font"].lower(),
                        })
            self.cache[index] = spans
        return self.cache[index]

    def inside(self, index, bbox):
        x0, y0, x1, y1 = bbox
        return [s for s in self.on_page(index)
                if x0 - PAD <= s["cx"] <= x1 + PAD and y0 - PAD <= s["cy"] <= y1 + PAD]


def _to_points(bbox, page: Page, scale: str):
    x0, y0, x1, y1 = bbox
    if scale == "points":
        return (float(x0), float(y0), float(x1), float(y1))
    return (x0 * page.width / 1000, y0 * page.height / 1000,
            x1 * page.width / 1000, y1 * page.height / 1000)


def _overlap(text: str, spans) -> float:
    joined = clean_text(" ".join(s["text"] for s in spans))
    return fuzz.ratio(text.lower(), joined.lower())


def _detect_scale(items, pages, spans: _Spans) -> str:
    cands = [it for it in items if it.get("type") == "text" and len(item_text(it)) >= 20
             and 0 <= it.get("page_idx", -1) < len(pages)]
    step = max(1, len(cands) // SCALE_SAMPLE)
    sample = cands[::step][:SCALE_SAMPLE]
    if not sample:
        return "per_mille"
    means = {}
    for scale in ("per_mille", "points"):
        scores = [_overlap(clean_text(item_text(it)),
                           spans.inside(it["page_idx"], _to_points(it["bbox"], pages[it["page_idx"]], scale)))
                  for it in sample]
        means[scale] = statistics.fmean(scores)
    return "points" if means["points"] > means["per_mille"] else "per_mille"


def _font_stats(spans):
    weighted = sorted((s["size"], max(len(s["text"]), 1)) for s in spans)
    total = sum(n for _, n in weighted)
    if total == 0:
        return None, None
    acc = 0
    size = weighted[-1][0]
    for sz, n in weighted:
        acc += n
        if acc * 2 >= total:
            size = sz
            break
    bold_chars = sum(max(len(s["text"]), 1) for s in spans if s["bold"])
    return size, bold_chars / total >= 0.6


def build_layout(pdf_path, content_list_path, doc_id=None) -> LayoutDoc:
    pdf_path, content_list_path = Path(pdf_path), Path(content_list_path)
    items = json.loads(content_list_path.read_text(encoding="utf-8"))
    doc = pymupdf.open(pdf_path)
    pages = [Page(index=i, width=p.rect.width, height=p.rect.height) for i, p in enumerate(doc)]
    spans = _Spans(doc)
    scale = _detect_scale(items, pages, spans)

    blocks, order, overlaps = [], {}, []
    for item in items:
        page_idx = item.get("page_idx", 0)
        if not (0 <= page_idx < len(pages)) or not item.get("bbox"):
            continue
        page = pages[page_idx]
        bbox = _to_points(item["bbox"], page, scale)
        raw = item_text(item)
        text = clean_text(raw)
        hits = spans.inside(page_idx, bbox)
        size, bold = _font_stats(hits)
        if item.get("type") == "text" and text:
            overlaps.append(_overlap(text, hits))
        n = order.get(page_idx, 0)
        order[page_idx] = n + 1
        blocks.append(Block(
            id=f"p{page_idx:04d}-b{n:03d}", page=page_idx, order=n,
            type=to_block_type(item.get("type")), text=text, raw_text=raw, bbox=bbox,
            font_size=size, bold=bold, parser_level=item.get("text_level"), n_chars=len(text),
        ))

    outline = [OutlineEntry(title=t, level=lvl, page=max(p - 1, 0)) for lvl, t, p in doc.get_toc(simple=True)]
    layout = LayoutDoc(
        doc_id=doc_id or slugify(pdf_path.stem),
        source={
            "parser": "mineru",
            "pdf": pdf_path.name,
            "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "content_list": content_list_path.name,
            "bbox_scale_detected": scale,
            "median_text_overlap": statistics.median(overlaps) if overlaps else None,
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        pages=pages, blocks=blocks, embedded_outline=outline,
    )
    doc.close()
    return layout


def summary(layout: LayoutDoc) -> str:
    b = layout.blocks
    return (f"pages={layout.page_count} blocks={len(b)} "
            f"headings_hinted={sum(x.parser_level is not None for x in b)} "
            f"with_font_size={sum(x.font_size is not None for x in b)} "
            f"bbox_scale={layout.source['bbox_scale_detected']} "
            f"outline_entries={len(layout.embedded_outline)} "
            f"median_text_overlap={layout.source['median_text_overlap']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build layout.json from a PDF and its MinerU content_list.json")
    ap.add_argument("pdf")
    ap.add_argument("content_list")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--doc-id")
    args = ap.parse_args(argv)
    layout = build_layout(args.pdf, args.content_list, args.doc_id)
    Path(args.out).write_text(layout.model_dump_json(indent=1), encoding="utf-8")
    print(summary(layout))


if __name__ == "__main__":
    main()
