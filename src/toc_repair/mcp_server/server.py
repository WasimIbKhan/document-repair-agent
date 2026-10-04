import functools
from typing import Literal, TypedDict

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from toc_repair.pdf_tools import list_cases, open_case

server = MCPServer(
    "toc-repair",
    instructions=(
        "Read-only tools over the PDFs of the toc-repair eval cases. Pages are 1-based, as a PDF viewer "
        "shows them, never the printed folio. Every text line has an id like p234-l03 (page 234, line 3); "
        "cite those ids, never coordinates. Start with pdf_summary, read the contents with pdf_contents_view, "
        "link its entries with pdf_find_headings, and check doubtful rows with pdf_find_heading."
    ),
)
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)


def tool(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as err:
            raise ToolError(str(err)) from None

    return server.tool(annotations=READ_ONLY)(wrapper)


@tool
def pdf_list_cases() -> list[str]:
    """List the doc ids you can open: eval cases whose layout.json and PDF are both present.
    Pass one of these as doc_id to every other pdf_* tool."""
    return list_cases()


@tool
def pdf_summary(doc_id: str) -> dict:
    """Overview of one PDF; call this first. Returns: pages (count); body_font_size and heading_font_sizes
    (sizes larger than body, with character counts, so you know what a heading looks like); page_map
    (offset = PDF page minus printed page number, with agreement share, exceptions as [pdf_page, printed],
    and the PDF page range carrying roman folios); contents_pages (likely table-of-contents pages, 1-based);
    running_lines (normalized texts in the top or bottom margin that repeat on 3+ pages, with page counts)."""
    return open_case(doc_id).summary()


@tool
def pdf_page_lines(doc_id: str, page: int, detail: Literal["concise", "detailed"] = "concise") -> list[dict]:
    """All text lines of one PDF page (1-based, as a viewer shows it) in reading order, from the PDF's own
    text layer. Each line: id (cite this), text, size (pt), bold; margin=true when it sits in the top or
    bottom 8% of the page, and repeats_on_pages=n when the same margin text appears on n pages. These are
    facts, not verdicts: a chapter title can also appear as a running header, so decide from size,
    position and context. detail="detailed" adds bbox, font and y (top of the line as a fraction of page height). Use it to
    check a contents page that pdf_contents_view got wrong, or to inspect a page when pdf_find_heading's
    candidates are unclear."""
    return open_case(doc_id).page_lines(page, detail)


@tool
def pdf_search(doc_id: str, pattern: str, regex: bool = False) -> dict:
    """Search every line of the PDF. Default: case- and accent-insensitive phrase match (punctuation ignored,
    so "tafsir" finds "Tafsīr"). regex=true applies a case-insensitive Python regex to the raw text instead.
    Returns up to 20 matches as {id, page, text}, body matches first, then matches in the top or bottom
    margin flagged margin=true with repeats_on_pages=n (usually running headers); total counts all matches
    and margin_matches the margin ones. Narrow the phrase if truncated. Use it when you do not know roughly
    where a heading is; otherwise prefer pdf_find_heading."""
    return open_case(doc_id).search(pattern, regex)


@tool
def pdf_find_heading(doc_id: str, title: str, near_page: int, window: int = 3) -> dict:
    """Find where one contents entry's heading sits in the body. Scans PDF pages near_page-window ..
    near_page+window (1-based; convert a printed page with pdf_page_for_printed first) and ranks lines by
    fuzzy match to the title, also trying runs of up to 3 consecutive lines, so a heading split over lines
    ("Part One" / "VISION") comes back as one candidate with every id in order. Returns up to 5 candidates:
    ids (cite all of them), page, text, score (0-100), size, bold, margin/repeats_on_pages when it sits in
    the page margin (often a running header, not the heading), and the neighbouring lines as context. A real
    heading is usually larger or bold compared to body_font_size. To link a whole contents page at once,
    use pdf_find_headings."""
    return open_case(doc_id).find_heading(title, near_page, window)


class HeadingQuery(TypedDict):
    title: str
    near_page: int


@tool
def pdf_find_headings(doc_id: str, items: list[HeadingQuery], window: int = 3) -> list[dict]:
    """Link many contents entries in one call: the same ranking as pdf_find_heading, for up to 100 items
    of {title, near_page} (near_page is the 1-based PDF page, ±window pages are searched). Returns one
    compact row per item, in order: {title, near_page, best, runner_up}. best is {ids, page, text, score,
    size, bold, margin?, repeats_on_pages?} or null; runner_up is {ids, page, score, size, margin?} or null.
    No context lines. Accept rows whose best is strong and clearly ahead; check rows whose best has a weak
    score, sits in the margin, or is close to the runner-up with pdf_find_heading or pdf_page_lines.
    A bad item gets an error field instead of failing the whole call."""
    return open_case(doc_id).find_headings(items, window)


@tool
def pdf_contents_view(doc_id: str, page: int) -> dict:
    """Read one contents page (1-based PDF page) as rows: {title, printed_page (int or null), indent, x0,
    ids}. A row is a title plus the page number at the end of its line or on the same visual row; a title
    that wraps onto the next line is joined into one row, and ids lists every line used. indent is the
    level (1 = leftmost) from clustering the rows' left x positions across this run of contents pages, so
    levels agree between pages; indent_x0 gives the x position of each level. Check the rows against
    pdf_page_lines when a contents page has no page numbers or several entries on one line."""
    return open_case(doc_id).contents_view(page)


@tool
def pdf_page_for_printed(doc_id: str, printed_page: int) -> dict:
    """Convert a printed page number (as written in the contents page) to the 1-based PDF page. Uses the page
    that actually prints that number if there is one, otherwise the book's dominant offset."""
    doc = open_case(doc_id)
    pdf_page = doc.pdf_page_for(printed_page)
    pm = doc.page_map()
    if pdf_page is None:
        raise ToolError(f"cannot map printed page {printed_page}: page_map offset is {pm['offset']} over "
                        f"{pm['numbered_pages']} numbered pages; use pdf_search for the title instead")
    return {"printed_page": printed_page, "pdf_page": pdf_page, "offset": pm["offset"], "agreement": pm["agreement"]}


@tool
def pdf_render_page(doc_id: str, page: int) -> Image:
    """Render one PDF page (1-based) as a PNG image. Expensive in tokens: use it only when the text lines are
    ambiguous, e.g. to see indentation or layout that the text layer does not show."""
    return Image(data=open_case(doc_id).render_page(page), format="png")


if __name__ == "__main__":
    server.run("stdio")
