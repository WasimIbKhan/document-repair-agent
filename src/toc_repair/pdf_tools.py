import math
import re
from collections import Counter
from pathlib import Path

import pymupdf
from rapidfuzz import fuzz

from .schema import normalize

ROOT = Path(__file__).resolve().parents[2]
CASES_DIR = ROOT / "evals" / "cases"
RAW_DIR = ROOT / "data" / "raw"
BAND = 0.08
PARTIAL_CAP = 95
_ARABIC = re.compile(r"^\d{1,4}$")
_ROMAN = re.compile(r"^(x{0,3})(ix|iv|v?i{0,3})$", re.I)
_ENDS_NUMBER = re.compile(r"\d{1,4}$")
_EDGE_NUMBER = re.compile(r"^\d+\s+|\s+\d+$")
_PDF_KEY = re.compile(r'"pdf"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _roman_value(text: str) -> int | None:
    m = _ROMAN.match(text)
    if not m or not text:
        return None
    tens, rest = m.groups()
    ones = {"": 0, "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9}
    return 10 * len(tens) + ones[rest.lower()]


def _score(q: str, t: str) -> float:
    score = fuzz.ratio(q, t)
    if 0.5 * len(q) <= len(t) <= 3 * len(q):
        score = max(score, min(fuzz.partial_ratio(q, t), PARTIAL_CAP))
    return score


class PdfDoc:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._doc = None
        self._lines: dict[int, list[dict]] = {}
        self._running: dict[str, int] | None = None
        self._page_map: dict | None = None
        self._folios: dict[int, int] = {}

    @property
    def doc(self) -> pymupdf.Document:
        if self._doc is None:
            self._doc = pymupdf.open(self.path)
        return self._doc

    @property
    def page_count(self) -> int:
        return len(self.doc)

    def _check(self, page) -> int:
        if not isinstance(page, int) or isinstance(page, bool):
            raise ValueError(f"page must be an integer, got {page!r}")
        if not 1 <= page <= self.page_count:
            raise ValueError(f"page {page} is out of range; this PDF has {self.page_count} pages (pages are 1-based)")
        return page

    def _raw_lines(self, page: int) -> list[dict]:
        if page not in self._lines:
            pg = self.doc[page - 1]
            height = pg.rect.height or 1
            out = []
            for block in pg.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = line["spans"]
                    text = " ".join("".join(s["text"] for s in spans).split())
                    x0, y0, x1, y1 = line["bbox"]
                    if not text or not 0 <= (y0 + y1) / 2 <= height:
                        continue
                    norm = normalize(text)
                    out.append({
                        "id": f"p{page:03d}-l{len(out):02d}", "page": page, "index": len(out), "text": text,
                        "norm": norm, "key": _EDGE_NUMBER.sub("", norm),
                        "size": round(max(s["size"] for s in spans), 1),
                        "bold": any(s["flags"] & 16 or "bold" in s["font"].lower() for s in spans),
                        "font": max(spans, key=lambda s: len(s["text"]))["font"],
                        "bbox": [round(v, 1) for v in (x0, y0, x1, y1)],
                        "y": round(y0 / height, 3),
                        "band": y0 < BAND * height or y1 > (1 - BAND) * height,
                    })
            self._lines[page] = out
        return self._lines[page]

    def _all_lines(self):
        for p in range(1, self.page_count + 1):
            yield from self._raw_lines(p)

    @property
    def running(self) -> dict[str, int]:
        if self._running is None:
            pages: dict[str, set[int]] = {}
            for ln in self._all_lines():
                if ln["band"] and ln["key"] and not ln["key"].isdigit():
                    pages.setdefault(ln["key"], set()).add(ln["page"])
            self._running = {t: len(ps) for t, ps in pages.items() if len(ps) >= 3}
        return self._running

    def _is_running(self, ln: dict) -> bool:
        return ln["band"] and ln["key"] in self.running

    def page_map(self) -> dict:
        if self._page_map is not None:
            return self._page_map
        arabic: dict[int, list[int]] = {}
        roman: list[int] = []
        for p in range(1, self.page_count + 1):
            band = [ln for ln in self._raw_lines(p) if ln["band"]]
            band.sort(key=lambda ln: ln["y"] < 0.5)
            nums = [int(ln["text"]) for ln in band if _ARABIC.match(ln["text"])]
            if nums:
                arabic[p] = nums
            elif any(_roman_value(ln["text"]) for ln in band):
                roman.append(p)
        votes = Counter(off for p, nums in arabic.items() for off in {p - n for n in nums})
        offset = votes.most_common(1)[0][0] if votes else None
        folios = {p: next((n for n in nums if p - n == offset), nums[0]) for p, nums in arabic.items()}
        self._folios = folios
        agree = sum(p - n == offset for p, n in folios.items())
        self._page_map = {
            "offset": offset,
            "agreement": round(agree / len(folios), 3) if folios else None,
            "numbered_pages": len(folios),
            "exceptions": [[p, n] for p, n in folios.items() if p - n != offset][:10],
            "roman_pages": [roman[0], roman[-1]] if roman else None,
        }
        return self._page_map

    def pdf_page_for(self, printed: int) -> int | None:
        offset = self.page_map()["offset"]
        exact = [p for p, n in self._folios.items() if n == printed]
        if exact:
            guess = printed + (offset or 0)
            return min(exact, key=lambda p: abs(p - guess))
        if offset is None or not 1 <= printed + offset <= self.page_count:
            return None
        return printed + offset

    def contents_pages(self) -> list[dict]:
        limit = min(self.page_count, max(25, math.ceil(0.15 * self.page_count)))
        out = []
        for p in range(1, limit + 1):
            lines = self._raw_lines(p)
            if any(ln["norm"] in ("contents", "table of contents") for ln in lines):
                out.append({"page": p, "reason": "title"})
            elif len(lines) >= 6 and sum(bool(_ENDS_NUMBER.search(ln["text"])) for ln in lines) >= 0.4 * len(lines):
                out.append({"page": p, "reason": "numbered"})
        return out

    def summary(self) -> dict:
        sizes: Counter[float] = Counter()
        for ln in self._all_lines():
            sizes[round(ln["size"] * 2) / 2] += len(ln["text"])
        body = sizes.most_common(1)[0][0] if sizes else None
        headings = sorted(((s, c) for s, c in sizes.items() if body is not None and s > body + 1),
                          key=lambda sc: -sc[1])[:6]
        running = sorted(self.running.items(), key=lambda kv: -kv[1])[:10]
        return {
            "pages": self.page_count,
            "body_font_size": body,
            "heading_font_sizes": [{"size": s, "chars": c} for s, c in sorted(headings, reverse=True)],
            "page_map": self.page_map(),
            "contents_pages": self.contents_pages(),
            "running_lines": [{"text": t, "pages": n} for t, n in running],
        }

    def _view(self, ln: dict, detail: str) -> dict:
        out = {"id": ln["id"], "text": ln["text"], "size": ln["size"], "bold": ln["bold"]}
        if detail == "detailed":
            out.update(bbox=ln["bbox"], font=ln["font"], y=ln["y"])
        if self._is_running(ln):
            out["running"] = True
        return out

    def page_lines(self, page: int, detail: str = "concise") -> list[dict]:
        if detail not in ("concise", "detailed"):
            raise ValueError(f"detail must be 'concise' or 'detailed', got {detail!r}")
        return [self._view(ln, detail) for ln in self._raw_lines(self._check(page))]

    def search(self, pattern: str, regex: bool = False, max_results: int = 20) -> dict:
        if regex:
            try:
                rx = re.compile(pattern, re.I)
            except re.error as err:
                raise ValueError(f"invalid regex {pattern!r}: {err}; fix it or search with regex=false") from None
            hit = lambda ln: rx.search(ln["text"])
        else:
            q = normalize(pattern)
            if not q:
                raise ValueError("pattern has no letters or digits; give a word or phrase from the heading")
            hit = lambda ln: q in ln["norm"]
        matches = [{"id": ln["id"], "page": ln["page"], "text": ln["text"]} for ln in self._all_lines() if hit(ln)]
        out = {"matches": matches[:max_results], "total": len(matches)}
        if len(matches) > max_results:
            out["note"] = (f"showing the first {max_results} of {len(matches)} matches; use a longer phrase, "
                           "or find_heading with near_page to search a few pages only")
        return out

    def find_heading(self, title: str, near_page: int, window: int = 3, max_results: int = 5) -> dict:
        self._check(near_page)
        q = normalize(title)
        if not q:
            raise ValueError("title has no letters or digits; give the heading text as printed in the contents")
        lo, hi = max(1, near_page - window), min(self.page_count, near_page + window)
        best: dict[str, tuple] = {}
        for p in range(lo, hi + 1):
            lines = self._raw_lines(p)
            keep = [ln for ln in lines if not self._is_running(ln)]
            for i, ln in enumerate(keep):
                groups = [[ln]] + ([[ln, keep[i + 1]]] if i + 1 < len(keep) else [])
                for g in groups:
                    score = _score(q, " ".join(x["norm"] for x in g))
                    size = max(x["size"] for x in g)
                    key = (-score, -size, abs(p - near_page), ln["index"])
                    if ln["id"] not in best or key < best[ln["id"]][0]:
                        best[ln["id"]] = (key, g, lines)
        ranked = sorted(best.values(), key=lambda v: v[0])[:max_results]
        cands = []
        for key, g, lines in ranked:
            first, last = g[0]["index"], g[-1]["index"]
            cands.append({
                "ids": [x["id"] for x in g], "page": g[0]["page"], "text": " / ".join(x["text"] for x in g),
                "score": round(-key[0], 1), "size": -key[1], "bold": any(x["bold"] for x in g),
                "context_before": lines[first - 1]["text"][:80] if first > 0 else "",
                "context_after": lines[last + 1]["text"][:80] if last + 1 < len(lines) else "",
            })
        return {"searched_pages": [lo, hi], "candidates": cands}

    def render_page(self, page: int, dpi: int = 80) -> bytes:
        return self.doc[self._check(page) - 1].get_pixmap(dpi=dpi).tobytes("png")


_DOCS: dict[str, PdfDoc] = {}


def _case_pdf_name(doc_id: str) -> str:
    layout = CASES_DIR / doc_id / "layout.json"
    if not layout.exists():
        known = ", ".join(sorted(d.name for d in CASES_DIR.iterdir() if (d / "layout.json").exists()))
        raise ValueError(f"unknown case {doc_id!r}; cases with a layout.json: {known}")
    with layout.open(encoding="utf-8") as f:
        m = _PDF_KEY.search(f.read(4096))
    if not m:
        raise ValueError(f"case {doc_id!r} has no source.pdf in its layout.json")
    return m.group(1)


def find_case_pdf(doc_id: str) -> Path:
    name = _case_pdf_name(doc_id)
    found = next((p for p in RAW_DIR.rglob("*.pdf") if p.name == name), None)
    if found is None:
        raise ValueError(f"case {doc_id!r} needs {name!r} but it is not under data/raw/; copy the PDF there")
    return found


def list_cases() -> list[str]:
    ok = []
    for d in sorted(CASES_DIR.iterdir()):
        try:
            find_case_pdf(d.name)
            ok.append(d.name)
        except ValueError:
            pass
    return ok


def open_case(doc_id: str) -> PdfDoc:
    if doc_id not in _DOCS:
        _DOCS[doc_id] = PdfDoc(find_case_pdf(doc_id))
    return _DOCS[doc_id]
