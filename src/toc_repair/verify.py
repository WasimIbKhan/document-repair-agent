import re

from rapidfuzz import fuzz

from .pdf_tools import PdfDoc
from .schema import LinkedEntry, contains_words, normalize

TITLE_MATCH = 90
_NUMBER_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|"
                 "sixteen|seventeen|eighteen|nineteen|twenty")
_LABEL = re.compile(rf"^(?:(?:chapter|part|section|book)\s+)?(?:\d+(?:\s\d+)*|[ivxlcdm]+|{_NUMBER_WORDS})\s+")


def strip_label(norm: str) -> str:
    return _LABEL.sub("", norm, count=1)


def title_score(title: str, text: str) -> float:
    a, b = normalize(title), normalize(text)
    return max(fuzz.ratio(a, b), fuzz.ratio(strip_label(a), strip_label(b)))


def _check_lines(doc: PdfDoc, n: int, e: LinkedEntry) -> str | None:
    name = f"entry {n} {e.title!r}"
    lines = []
    for lid in e.line_ids:
        ln = doc.line(lid)
        if ln is None:
            return (f"{name}: line id {lid!r} does not exist; cite ids exactly as page_lines or find_headings "
                    f"return them, like p012-l03")
        if ln["page"] != e.page:
            return f"{name}: line {ln['id']} is on page {ln['page']}, not page {e.page}; page must be the page of the cited lines"
        lines.append(ln)
    if any(b["index"] != a["index"] + 1 for a, b in zip(lines, lines[1:])):
        return f"{name}: lines {', '.join(e.line_ids)} are not adjacent; cite one line or a run of consecutive lines in order"
    text = " ".join(ln["text"] for ln in lines)
    score = title_score(e.title, text)
    if score < TITLE_MATCH:
        return (f"{name}: lines {', '.join(ln['id'] for ln in lines)} read {text!r} (score {score:.0f}); "
                f"cite the line(s) that hold exactly this heading")
    return None


def _contents_rows(doc: PdfDoc) -> list[dict]:
    return [{**r, "page": c["page"]} for c in doc.contents_pages() for r in doc.contents_view(c["page"])["rows"]]


def verify(doc: PdfDoc, entries: list[LinkedEntry], unresolved: list[dict] | None = None) -> list[str]:
    if not entries:
        return ["no entries: propose every contents entry, linked to its heading in the body"]
    errors = [err for n, e in enumerate(entries, 1) if (err := _check_lines(doc, n, e))]
    for n, (prev, e) in enumerate(zip([None, *entries], entries), 1):
        if prev is None and e.level > 2:
            errors.append(f"entry 1 {e.title!r}: level {e.level} jumps more than one deeper than the top level; "
                          f"the first entry may be level 1 or 2")
        elif prev is not None and e.level > prev.level + 1:
            errors.append(f"entry {n} {e.title!r}: level {e.level} jumps more than one deeper than entry {n - 1} "
                          f"{prev.title!r} at level {prev.level}")
        if prev is not None and e.page < prev.page:
            errors.append(f"entry {n} {e.title!r}: page {e.page} comes before entry {n - 1} {prev.title!r} on page "
                          f"{prev.page}; entries must follow the book's order")
    rows = _contents_rows(doc)
    for n, e in enumerate(entries, 1):
        q = normalize(e.title)
        scored = [(fuzz.ratio(q, normalize(r["title"])), r) for r in rows]
        matches = [r for s, r in scored if s >= TITLE_MATCH]
        if matches and e.level not in {r["indent"] for r in matches}:
            best = max(scored, key=lambda sr: sr[0])[1]
            errors.append(f"entry {n} {e.title!r}: level {e.level}, but the contents page (PDF page {best['page']}) "
                          f"indents {best['title']!r} at level {best['indent']}; levels follow the author's "
                          f"contents indentation, not what reads naturally")
    return errors + _missing(rows, [e.title for e in entries] + [str(u.get("title", "")) for u in unresolved or []])


def _contained(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    return len(short.split()) >= 2 and contains_words(short, long)


def _missing(rows: list[dict], titles: list[str]) -> list[str]:
    free = {i: normalize(t) for i, t in enumerate(titles)}
    missing = []
    for r in rows:
        q = normalize(r["title"])
        best = max(free, key=lambda i: fuzz.ratio(q, free[i]), default=None)
        if best is not None and fuzz.ratio(q, free[best]) >= TITLE_MATCH:
            del free[best]
        else:
            missing.append(r)
    errors = []
    for r in missing:
        q = normalize(r["title"])
        hit = next((i for i, t in free.items() if _contained(q, t)), None)
        if hit is not None:
            del free[hit]
            continue
        where = f"printed page {r['printed_page']}" if r["printed_page"] is not None else f"listed on contents PDF page {r['page']}"
        errors.append(f"contents entry {r['title']!r} ({where}) is missing; add it as an entry, or list it in "
                      f"unresolved with a reason (e.g. 'not a heading: translator's note', 'no heading in the body')")
    return errors
