import json
import os
import time
from datetime import datetime
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, ResultMessage, query

from ..pdf_tools import CASES_DIR, ROOT, PdfDoc, open_case
from ..schema import AgentResult
from .tools import SERVER, RunState, allowed_tool_names, fallback_toc, make_server

DEFAULT_MODEL = "claude-sonnet-5"
ENV_FILE = ROOT.parents[1] / "backend" / "server" / ".env"
PREFIX = f"mcp__{SERVER}__"
SYSTEM_PROMPT = """You link a book's table of contents to the exact places where each heading appears in the PDF. The printed contents page is the source of truth for titles and levels; the PDF's own text is the source of truth for where headings are. Work in this order:
1. Call summary to get the page map (printed page number to PDF page) and the likely contents pages.
2. Call contents_view on the contents pages to get each entry's title, printed page and indent level. Contents pages can run over several pages; include every one. If there is no usable contents page, build the list from the body using search and page_lines.
3. Call find_headings once with every entry (near_page = printed page + offset; null when the book prints no page numbers, which searches forward from the previous entry). Rows with status "ok" are linked; trust them.
4. Investigate only rows with status "check", using find_heading, page_lines, and render_page only when the text cannot settle it (page images are expensive).
5. Call propose_toc with every entry: title exactly as printed on the contents page, level, PDF page, the line_ids you are citing, and a role when clear (part, chapter, section, front_matter, back_matter). Every contents entry must be either linked or listed in unresolved with a reason (e.g. 'not a heading: translator's note', 'no heading in the body'). A checker verifies every entry against the PDF; if it returns errors, fix them and propose once more.
You have a hard budget of 40 tool calls and 20 page images. Never answer in prose; propose_toc is the only way to answer."""


def api_key() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            name, _, value = line.strip().partition("=")
            if name.strip() == "ANTHROPIC_API_KEY" and value.strip():
                return value.strip().strip("'\"")
    return None


def make_hooks(state: RunState) -> dict:
    def deny(reason: str) -> dict:
        state.denied += 1
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    async def pre(data, tool_use_id, context):
        name = data.get("tool_name", "").removeprefix(PREFIX)
        if name != "propose_toc":
            if state.tool_calls >= state.max_tool_calls:
                return deny(f"tool budget used up ({state.max_tool_calls} calls); call propose_toc now with what "
                            f"you have, listing entries you could not link in unresolved")
            if name == "render_page" and state.images >= state.max_images:
                return deny(f"page image budget used up ({state.max_images} images); settle it from the text "
                            f"with page_lines or find_heading")
        state.tool_calls += 1
        state.calls[name] += 1
        state.images += name == "render_page"
        state.started[tool_use_id or data.get("tool_use_id")] = time.perf_counter()
        return {}

    async def post(data, tool_use_id, context):
        started = state.started.pop(tool_use_id or data.get("tool_use_id"), None)
        output = data.get("tool_response", data.get("error"))
        record = {"t": datetime.now().isoformat(timespec="seconds"),
                  "tool": data.get("tool_name", "").removeprefix(PREFIX),
                  "input": json.dumps(data.get("tool_input"), ensure_ascii=False)[:300],
                  "output_chars": len(output if isinstance(output, str) else json.dumps(output, default=str)),
                  "ms": round((time.perf_counter() - started) * 1000) if started else None}
        if state.log_path:
            with Path(state.log_path).open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {}

    return {"PreToolUse": [HookMatcher(hooks=[pre])], "PostToolUse": [HookMatcher(hooks=[post])],
            "PostToolUseFailure": [HookMatcher(hooks=[post])]}


def build_options(doc: PdfDoc, state: RunState, model: str, max_budget_usd: float, max_turns: int,
                  key: str | None) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={SERVER: make_server(doc, state)},
        strict_mcp_config=True,
        tools=[],
        allowed_tools=allowed_tool_names(),
        setting_sources=[],
        skills=[],
        permission_mode="dontAsk",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        model=model,
        cwd=str(ROOT),
        hooks=make_hooks(state),
        env={"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
             **({"ANTHROPIC_API_KEY": key} if key else {})},
    )


def review_text(result: AgentResult, model: str, stamp: str) -> str:
    out = [
        "# Agent-proposed TOC. Correct this file, then save it as ground_truth_toc.txt",
        "# One entry per line:  Title @ <1-based PDF page>, nested with 2 spaces per level.",
        f"# doc: {result.doc_id}",
        f"# model: {model}",
        f"# timestamp: {stamp}",
        f"# source: toc-repair-agent ({result.source}), not yet human-verified",
        f"# needs_human: {str(result.needs_human).lower()}",
        f"# confidence: {result.confidence}",
        *[f"# problem: {p}" for p in result.problems],
        "",
    ]
    for e in result.entries:
        indent = "  " * (e.level - 1)
        if e.role:
            out.append(f"{indent}# role: {e.role}")
        out.append(f"{indent}{e.title.strip()} @ {e.page}")
    if result.unresolved:
        out += ["", "# unresolved:"] + [f"#   {json.dumps(u, ensure_ascii=False)}" for u in result.unresolved]
    return "\n".join(out) + "\n"


async def run_book(doc_id: str, model: str = DEFAULT_MODEL, max_tool_calls: int = 40, max_images: int = 20,
                   max_budget_usd: float = 1.50, max_turns: int = 30) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError(f"no ANTHROPIC_API_KEY in the environment or in {ENV_FILE}")
    doc = open_case(doc_id)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "runs" / doc_id / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    state = RunState(doc_id, max_tool_calls, max_images, log_path=run_dir / "calls.jsonl")
    options = build_options(doc, state, model, max_budget_usd, max_turns, key)
    prompt = f"Link the table of contents of book {doc_id!r} ({doc.page_count} PDF pages) to its headings."
    started, final, error = time.perf_counter(), None, None
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                final = message
    except Exception as err:
        error = f"{type(err).__name__}: {err}"
    if state.result is None:
        state.result = fallback_toc(doc, doc_id)
    result = state.result
    stats = {
        "doc_id": doc_id, "model": model, "final_source": result.source, "entries": len(result.entries),
        "unresolved": len(result.unresolved), "problems": len(result.problems), "needs_human": result.needs_human, "confidence": result.confidence,
        "verification_attempts": state.attempts, "tool_calls": state.tool_calls, "images": state.images,
        "denied": state.denied, "calls_by_tool": dict(state.calls),
        "turns": final.num_turns if final else None,
        "wall_time_s": round(time.perf_counter() - started, 1),
        "cost_usd": final.total_cost_usd if final else None,
        "subtype": final.subtype if final else None, "terminal_reason": final.terminal_reason if final else None,
        "duration_ms": final.duration_ms if final else None,
        "usage": final.usage if final else None, "model_usage": final.model_usage if final else None,
        "error": error, "run_dir": str(run_dir),
    }
    (run_dir / "result.json").write_text(
        json.dumps({"result": result.model_dump(), "stats": stats}, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    (CASES_DIR / doc_id / "agent_review_toc.txt").write_text(review_text(result, model, stamp), encoding="utf-8")
    return stats
