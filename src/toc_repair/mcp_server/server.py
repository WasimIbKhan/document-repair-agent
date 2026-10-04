import functools
from typing import Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from toc_repair.pdf_tools import list_cases, open_case

server = MCPServer(
    "toc-repair",
    instructions=(
        "Read-only tools over the PDFs of the toc-repair eval cases. Pages are 1-based, as a PDF viewer "
        "shows them, never the printed folio. Every text line has an id like p234-l03 (page 234, line 3); "
        "cite those ids, never coordinates. Start with pdf_summary, then pdf_find_heading per contents entry."
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
    read a contents page or to inspect a page when pdf_find_heading's candidates are unclear."""
    return open_case(doc_id).page_lines(page, detail)


@tool
def pdf_search(doc_id: str, pattern: str, regex: bool = False) -> dict:
    """Search every line of the PDF. Default: case- and accent-insensitive phrase match (punctuation ignored,
    so "tafsir" finds "Tafsīr"). regex=true applies a case-insensitive Python regex to the raw text instead.
    Returns up to 20 matches as {id, page, text} plus the total; narrow the phrase if truncated. Use it when
    you do not know roughly where a heading is; otherwise prefer pdf_find_heading."""
    return open_case(doc_id).search(pattern, regex)


@tool
def pdf_find_heading(doc_id: str, title: str, near_page: int, window: int = 3) -> dict:
    """Find where a contents entry's heading sits in the body. Scans PDF pages near_page-window ..
    near_page+window (1-based; convert a printed page with pdf_page_for_printed first) and ranks lines by
    fuzzy match to the title, also trying each line joined with the next one, so headings split over two
    lines ("Part One" / "VISION") come back as one candidate with two ids. Running headers are skipped.
    Returns up to 5 candidates: ids (cite all of them), page, text, score (0-100), size, bold, and the
    neighbouring lines as context. A real heading is usually larger or bold compared to body_font_size."""
    return open_case(doc_id).find_heading(title, near_page, window)


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
