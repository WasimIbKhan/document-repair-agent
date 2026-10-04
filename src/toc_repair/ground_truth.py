import argparse
import json
import re
import sys
from pathlib import Path

from rapidfuzz import fuzz

from .schema import LayoutDoc, normalize

CASES_DIR = Path(__file__).resolve().parents[2] / "evals" / "cases"
PARTIAL_CAP = 95
FURNITURE = {"header", "footer", "page_number"}
_ENTRY = re.compile(r"^(?P<title>.*?)\s*@\s*(?P<page>\S+?)(?:\s*\[(?P<pin>p\d{4}-b\d{3})\])?\s*$")


class GroundTruthError(ValueError):
    def __init__(self, line: int, msg: str):
        super().__init__(f"line {line}: {msg}")
        self.line = line


_SOURCE = re.compile(r"^#\s*source:\s*(?P<source>.+?)\s*$", re.I)


def _norm(text: str) -> str:
    return normalize(text)


def parse_source(text: str) -> str:
    for line in text.splitlines():
        m = _SOURCE.match(line.strip())
        if m:
            return m.group("source")
    return "hand-written"


def parse_ground_truth_text(text: str) -> list[dict]:
    entries, prev_level = [], 0
    for no, raw in enumerate(text.splitlines(), 1):
        line = raw.replace("\t", "  ").rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2:
            raise GroundTruthError(no, f"indentation of {indent} spaces is not a multiple of 2")
        level = indent // 2 + 1
        if level > prev_level + 1:
            raise GroundTruthError(no, f"level {level} jumps more than one deeper than previous level {prev_level}")
        m = _ENTRY.match(line.strip())
        if not m or not m.group("title"):
            raise GroundTruthError(no, "expected 'Title @ <page>'")
        try:
            page = int(m.group("page"))
        except ValueError:
            raise GroundTruthError(no, f"page {m.group('page')!r} is not an integer") from None
        if page < 1:
            raise GroundTruthError(no, f"page {page} must be >= 1")
        entries.append({"title": m.group("title"), "level": level, "page": page - 1, "line": no,
                        "pin": m.group("pin")})
        prev_level = level
    return entries


def _score(title_norm: str, block_norm: str) -> float:
    score = fuzz.ratio(title_norm, block_norm)
    if 0.5 * len(title_norm) <= len(block_norm) <= 5 * len(title_norm):
        score = max(score, min(fuzz.partial_ratio(title_norm, block_norm), PARTIAL_CAP))
    return score


def running_texts(layout: LayoutDoc, min_pages: int = 3) -> set[str]:
    pages: dict[str, set[int]] = {}
    for b in layout.blocks:
        if b.text and b.type in FURNITURE:
            pages.setdefault(b.normalized_text, set()).add(b.page)
    return {t for t, ps in pages.items() if len(ps) >= min_pages}


def _ranked(title: str, blocks: list) -> list[tuple[float, object]]:
    q = _norm(title)
    scored = [(_score(q, b.normalized_text), b) for b in blocks]
    return sorted(scored, key=lambda s: (-s[0], -(s[1].font_size or 0), s[1].order))


def resolve_against_layout(entries: list[dict], layout: LayoutDoc, threshold: float = 85) -> list[dict]:
    running = running_texts(layout)
    out = []
    for e in entries:
        page = e["page"]
        if not 0 <= page < layout.page_count:
            raise GroundTruthError(e.get("line", 0), f"page {page + 1} out of range 1..{layout.page_count}")
        best = None
        if e.get("pin"):
            block = layout.block_by_id(e["pin"])
            if block is None or abs(block.page - page) > 1:
                raise GroundTruthError(e.get("line", 0), f"pinned block {e['pin']} is not on page {page + 1} or next to it")
            best = (_score(_norm(e["title"]), block.normalized_text), block)
        for p in (page, page - 1, page + 1) if best is None else ():
            if not 0 <= p < layout.page_count:
                continue
            blocks = [b for b in layout.blocks_on_page(p) if b.text]
            body = [b for b in blocks if b.type not in FURNITURE]
            furniture = [b for b in blocks if b.type in FURNITURE and b.normalized_text not in running]
            for tier in (body, furniture):
                ranked = _ranked(e["title"], tier)
                if ranked and ranked[0][0] >= threshold:
                    best = ranked[0]
                    break
            if best:
                break
        same = _ranked(e["title"], [b for b in layout.blocks_on_page(page) if b.text])
        r = {**e, "block_id": None, "match_score": None, "matched_text": None,
             "candidates": [(b.text, round(s, 1)) for s, b in same[:3]]}
        if best:
            r.update(block_id=best[1].id, match_score=round(best[0], 1), matched_text=best[1].text,
                     block_type=best[1].type)
        out.append(r)
    return out


def build_report(resolved: list[dict]) -> list[str]:
    report, prev = [], None
    for r in resolved:
        loc = f"line {r.get('line', '?')} {r['title']!r} @ {r['page'] + 1}"
        if prev is not None and r["page"] < prev:
            report.append(f"WARN page decreases: {loc} (previous entry was on page {prev + 1})")
        prev = r["page"]
        if r["block_id"] is None:
            cands = "; ".join(f"{t[:60]!r}={s}" for t, s in r["candidates"]) or "(no text blocks on page)"
            report.append(f"WARN unmatched: {loc}; candidates on page: {cands}")
        elif r.get("block_type") in FURNITURE and not r.get("pin"):
            report.append(f"WARN matched page furniture: {loc} -> {r['block_id']} {r['block_type']} "
                          f"{r['matched_text'][:60]!r}; pin the real heading block if one exists")
        elif r["matched_text"] and _norm(r["matched_text"]) != _norm(r["title"]):
            report.append(f"note fuzzy: {loc} -> {r['block_id']} {r['matched_text'][:60]!r} score={r['match_score']}")
    return report


def write_ground_truth_json(path: Path, layout: LayoutDoc, resolved: list[dict], source: str = "hand-written") -> None:
    keys = ("title", "level", "page", "block_id", "match_score")
    doc = {"doc_id": layout.doc_id, "source": source,
           "entries": [{k: r[k] for k in keys} for r in resolved]}
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")


def is_template(text: str) -> bool:
    return all(not ln.strip() or ln.lstrip().startswith("#") for ln in text.splitlines())


def template_text(layout: LayoutDoc) -> str:
    return "\n".join([
        "# Ground-truth TOC. One entry per line:  Title @ <page>",
        "# <page> is the 1-based page number your PDF viewer shows (NOT the printed folio).",
        "# Nest with 2 spaces per level (level 1 = no indent; tabs count as 2 spaces).",
        "# Titles verbatim as printed, including numbering like '1.2 Foo'.",
        "# Blank lines and lines starting with # are ignored. '@ page' is required.",
        "# Run: toc-repair-gt <this dir>   to validate and write ground_truth_toc.json",
        f"# doc_id: {layout.doc_id}",
        f"# pdf: {layout.source.get('pdf', '?')}",
        f"# pages: {layout.page_count}",
        "",
    ])


def load_layout(case_dir: Path) -> LayoutDoc:
    return LayoutDoc.model_validate_json((case_dir / "layout.json").read_text(encoding="utf-8"))


def init_case(case_dir: Path) -> bool:
    txt = case_dir / "ground_truth_toc.txt"
    if txt.exists():
        return False
    txt.write_text(template_text(load_layout(case_dir)), encoding="utf-8")
    return True


def run_case(case_dir: Path, threshold: float = 85) -> int:
    layout = load_layout(case_dir)
    text = (case_dir / "ground_truth_toc.txt").read_text(encoding="utf-8")
    try:
        resolved = resolve_against_layout(parse_ground_truth_text(text), layout, threshold)
    except GroundTruthError as err:
        print(f"{case_dir.name}: ERROR {err}")
        return 1
    report = build_report(resolved)
    for line in report:
        print(f"{case_dir.name}: {line}")
    write_ground_truth_json(case_dir / "ground_truth_toc.json", layout, resolved, parse_source(text))
    matched = sum(r["block_id"] is not None for r in resolved)
    warnings = sum(l.startswith("WARN") for l in report)
    print(f"{case_dir.name}: entries={len(resolved)} matched={matched} "
          f"unmatched={len(resolved) - matched} warnings={warnings}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert a hand-written ground_truth_toc.txt into ground_truth_toc.json")
    ap.add_argument("case_dir", nargs="?")
    ap.add_argument("--init", action="store_true", help="write a template ground_truth_toc.txt if absent")
    ap.add_argument("--all", action="store_true", help="run every case under evals/cases with a filled-in txt")
    ap.add_argument("--threshold", type=float, default=85)
    args = ap.parse_args(argv)

    if args.all:
        rc = 0
        for d in sorted(CASES_DIR.iterdir()):
            txt = d / "ground_truth_toc.txt"
            if (d / "layout.json").exists() and txt.exists() and not is_template(txt.read_text(encoding="utf-8")):
                rc |= run_case(d, args.threshold)
        sys.exit(rc)
    if not args.case_dir:
        ap.error("case_dir is required unless --all")
    case_dir = Path(args.case_dir)
    if args.init:
        print(f"{case_dir.name}: {'wrote' if init_case(case_dir) else 'kept existing'} ground_truth_toc.txt")
        return
    sys.exit(run_case(case_dir, args.threshold))


if __name__ == "__main__":
    main()
