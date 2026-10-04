import base64
import json
from collections import Counter
from dataclasses import dataclass, field

from claude_agent_sdk import SdkMcpTool, ToolAnnotations, create_sdk_mcp_server, tool
from pydantic import BaseModel, Field, ValidationError

from ..pdf_tools import MAX_BATCH, PdfDoc, open_case
from ..schema import AgentResult, LinkedEntry
from ..verify import verify

SERVER = "toc"
TOOL_NAMES = ("summary", "contents_view", "find_headings", "find_heading", "page_lines", "search", "render_page",
              "propose_toc")
MAX_ATTEMPTS = 2
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False, maxResultSizeChars=150_000)


@dataclass
class RunState:
    doc_id: str
    max_tool_calls: int = 40
    max_images: int = 20
    tool_calls: int = 0
    images: int = 0
    denied: int = 0
    attempts: int = 0
    calls: Counter = field(default_factory=Counter)
    started: dict = field(default_factory=dict)
    result: AgentResult | None = None
    log_path: object = None


class Proposal(BaseModel):
    entries: list[LinkedEntry]
    unresolved: list[dict] = []
    confidence: float = Field(ge=0, le=1)


def _text(obj) -> dict:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": text}]}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


PAGE = {"type": "integer", "minimum": 1, "description": "1-based PDF page, as a PDF viewer shows it"}
ENTRY = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "exactly as printed on the contents page"},
        "level": {"type": "integer", "minimum": 1},
        "page": PAGE,
        "line_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 6,
                     "description": "ids of the consecutive line(s) holding the heading, like p234-l02"},
        "role": {"enum": ["part", "chapter", "section", "front_matter", "back_matter", None]},
    },
    "required": ["title", "level", "page", "line_ids"],
}


def _validation_message(err: ValidationError) -> str:
    out = []
    for e in err.errors()[:20]:
        loc = list(e["loc"])
        if len(loc) >= 2 and loc[0] == "entries" and isinstance(loc[1], int):
            where = f"entry {loc[1] + 1}" + (f" field {'.'.join(map(str, loc[2:]))}" if loc[2:] else "")
        else:
            where = ".".join(map(str, loc)) or "input"
        out.append(f"{where}: {e['msg']}")
    return "; ".join(out)


def fallback_toc(doc: PdfDoc | str, doc_id: str | None = None) -> AgentResult:
    if isinstance(doc, str):
        doc_id, doc = doc, open_case(doc)
    pm = doc.page_map()
    numbered = pm["offset"] is not None and (pm["agreement"] or 0) >= 0.8 and pm["numbered_pages"] >= 10
    rows = [r for c in doc.contents_pages() for r in doc.contents_view(c["page"])["rows"]]
    items = [{"title": r["title"], "level": r["indent"],
              "near_page": doc.pdf_page_for(r["printed_page"]) if numbered and r["printed_page"] is not None else None}
             for r in rows]
    found = [f for i in range(0, len(items), MAX_BATCH) for f in doc.find_headings(items[i:i + MAX_BATCH])]
    entries, unresolved, prev = [], [], 0
    for r, f in zip(rows, found):
        if not f.get("best") or "not_found" in f["flags"]:
            unresolved.append({"title": r["title"], "printed_page": r["printed_page"], "reason": "no line scores >= 80"})
            continue
        prev = min(r["indent"], prev + 1)
        entries.append(LinkedEntry(title=r["title"], level=prev, page=f["best"]["page"], line_ids=f["best"]["ids"]))
    ok = sum(f["status"] == "ok" for f in found)
    return AgentResult(doc_id=doc_id or doc.path.stem, entries=entries, unresolved=unresolved,
                       confidence=round(ok / len(rows), 3) if rows else 0.0, needs_human=True, source="fallback")


def make_tools(doc: PdfDoc, state: RunState) -> list[SdkMcpTool]:
    def read_tool(name, description, schema):
        def wrap(fn):
            async def handler(args):
                try:
                    return _text(fn(args))
                except ValueError as err:
                    return _error(str(err))
            return tool(name, description, schema, annotations=READ_ONLY)(handler)
        return wrap

    @read_tool("summary", (
        "Overview of the book; call this first. Returns: pages (count); body_font_size and heading_font_sizes "
        "(sizes larger than body, with character counts); page_map (offset = PDF page minus printed page number, "
        "agreement share, exceptions as [pdf_page, printed], PDF page range with roman folios); contents_pages "
        "(likely table-of-contents pages, 1-based); running_lines (texts in the top or bottom margin repeating on "
        "3+ pages)."), _schema({}, []))
    def summary(args):
        return doc.summary()

    @read_tool("contents_view", (
        "Read one contents page as rows: {title, printed_page (int or null), indent, x0, ids}. A title that wraps "
        "onto the next line is joined into one row. indent is the level (1 = leftmost) from clustering the rows' "
        "left x positions across the whole run of contents pages, so levels agree between pages. Call it on every "
        "contents page. Check rows against page_lines when a contents page has no page numbers or several entries "
        "on one line."), _schema({"page": PAGE}, ["page"]))
    def contents_view(args):
        return doc.contents_view(args["page"])

    @read_tool("find_headings", (
        "Link many contents entries in one call, up to 100 items of {title, near_page, level}. near_page is the "
        "1-based PDF page (printed page + offset); ±window pages are searched. Set near_page to null when the book "
        "prints no page numbers: the search then runs forward from the page where the previous item was found "
        "(page 1 for the first, skipping contents pages) and takes the earliest page with a non-margin line scoring "
        ">= 90. Returns one row per item, in order: {title, near_page, level, searched_pages, best, runner_up, "
        "status, flags}. best is {ids, page, text, score, size, bold, margin?, repeats_on_pages?} or null. status "
        "is \"ok\" or \"check\"; flags say why: low_score (best < 95), in_margin, close_runner_up (a different line "
        "within 3 points), size_mismatch (best size more than 1pt off the median for its level in this batch), "
        "not_found (nothing scores >= 80). Trust \"ok\" rows; investigate \"check\" rows."),
        _schema({"items": {"type": "array", "maxItems": MAX_BATCH, "items": {
            "type": "object", "properties": {"title": {"type": "string"},
                                             "near_page": {"type": ["integer", "null"], "minimum": 1},
                                             "level": {"type": "integer", "minimum": 1}},
            "required": ["title", "near_page"]}},
            "window": {"type": "integer", "minimum": 0, "maximum": 10, "default": 3}}, ["items"]))
    def find_headings(args):
        return doc.find_headings(args["items"], args.get("window", 3))

    @read_tool("find_heading", (
        "Find where one heading sits in the body. Scans PDF pages near_page-window .. near_page+window and ranks "
        "lines by fuzzy match to the title, also trying runs of up to 3 consecutive lines, so a heading split over "
        "lines (\"Part One\" / \"VISION\") comes back as one candidate with every id in order. Returns up to 5 "
        "candidates: ids (cite all of them), page, text, score (0-100), size, bold, margin/repeats_on_pages when "
        "it sits in the page margin (often a running header, not the heading), and the neighbouring lines. A real "
        "heading is usually larger or bolder than body_font_size."),
        _schema({"title": {"type": "string"}, "near_page": PAGE,
                 "window": {"type": "integer", "minimum": 0, "maximum": 10, "default": 3}}, ["title", "near_page"]))
    def find_heading(args):
        return doc.find_heading(args["title"], args["near_page"], args.get("window", 3))

    @read_tool("page_lines", (
        "All text lines of one PDF page in reading order, from the PDF's own text layer. Each line: id (cite "
        "this), text, size (pt), bold; margin=true in the top or bottom 8% of the page, repeats_on_pages=n when "
        "the same margin text is on n pages. These are facts, not verdicts: a chapter title can also be a running "
        "header. detail=\"detailed\" adds bbox, font and y."),
        _schema({"page": PAGE, "detail": {"enum": ["concise", "detailed"], "default": "concise"}}, ["page"]))
    def page_lines(args):
        return doc.page_lines(args["page"], args.get("detail", "concise"))

    @read_tool("search", (
        "Search every line of the book. Default: case- and accent-insensitive phrase match (\"tafsir\" finds "
        "\"Tafsīr\"). regex=true applies a case-insensitive Python regex to the raw text. Returns up to 20 "
        "matches {id, page, text}, body matches first, then margin matches; total counts all matches. Use it when "
        "you do not know roughly where a heading is; otherwise prefer find_heading."),
        _schema({"pattern": {"type": "string"}, "regex": {"type": "boolean", "default": False}}, ["pattern"]))
    def search(args):
        return doc.search(args["pattern"], args.get("regex", False))

    @tool("render_page", (
        "Render one PDF page as a PNG image. Expensive: an image stays in the conversation and is paid for on "
        "every later turn. Use it only when the text lines cannot settle a question, such as indentation or "
        "layout the text layer does not show."), _schema({"page": PAGE}, ["page"]), annotations=READ_ONLY)
    async def render_page(args):
        try:
            png = doc.render_page(args["page"])
        except ValueError as err:
            return _error(str(err))
        return {"content": [{"type": "text", "text": f"page {args['page']}"},
                            {"type": "image", "data": base64.b64encode(png).decode("ascii"), "mimeType": "image/png"}]}

    @tool("propose_toc", (
        "Submit the linked table of contents; the only way to answer. entries, in book order: title exactly as "
        "printed on the contents page, level (1 = top; follow the contents page indentation, not what reads "
        "naturally), page (1-based PDF page of the heading), line_ids (the consecutive line ids holding the "
        "heading, from find_headings, find_heading or page_lines), role when clear. unresolved lists entries you "
        "could not link, as {title, reason}. confidence is 0..1. A checker verifies every entry against the PDF "
        "text: cited lines must read as the title, levels may deepen by at most one step, pages may not go "
        "backwards, and levels must match the contents indentation. If it returns errors you get one more "
        "attempt; a second failure records a code-only fallback for human review."),
        _schema({"entries": {"type": "array", "items": ENTRY},
                 "unresolved": {"type": "array", "items": {"type": "object"}},
                 "confidence": {"type": "number", "minimum": 0, "maximum": 1}}, ["entries", "confidence"]))
    async def propose_toc(args):
        if state.result is not None:
            return _error(f"a final result is already recorded (source={state.result.source}); stop here")
        accepted, message = propose(doc, state, args)
        return _text(message) if accepted else _error(message)

    return [summary, contents_view, find_headings, find_heading, page_lines, search, render_page, propose_toc]


def propose(doc: PdfDoc, state: RunState, args: dict) -> tuple[bool, str]:
    state.attempts += 1
    try:
        p = Proposal.model_validate(args)
        errors = verify(doc, p.entries)
    except ValidationError as err:
        p, errors = None, [f"invalid proposal: {_validation_message(err)}"]
    if not errors:
        state.result = AgentResult(doc_id=state.doc_id, entries=p.entries, unresolved=p.unresolved,
                                   confidence=p.confidence, needs_human=bool(p.unresolved), source="agent")
        return True, f"accepted: {len(p.entries)} entries"
    listing = "\n".join(f"- {e}" for e in errors[:40])
    more = f"\n(+{len(errors) - 40} more)" if len(errors) > 40 else ""
    if state.attempts < MAX_ATTEMPTS:
        return False, (f"rejected: {len(errors)} problem(s); entries are numbered from 1.\n{listing}{more}\n"
                f"Fix these and call propose_toc once more with the full list; this is your last attempt.")
    state.result = fallback_toc(doc, state.doc_id)
    return False, f"rejected twice; fallback recorded for human review.\n{listing}{more}"


def make_server(doc: PdfDoc, state: RunState):
    return create_sdk_mcp_server(SERVER, tools=make_tools(doc, state))


def allowed_tool_names() -> list[str]:
    return [f"mcp__{SERVER}__{n}" for n in TOOL_NAMES]
