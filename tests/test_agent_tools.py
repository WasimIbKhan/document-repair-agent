import asyncio
import base64
import json

import pytest
from test_verify import good_entries, make_book

from toc_repair.agent.run import make_hooks, review_text
from toc_repair.agent.tools import TOOL_NAMES, RunState, fallback_toc, make_tools
from toc_repair.ground_truth import parse_ground_truth_text


@pytest.fixture()
def book(tmp_path):
    return make_book(tmp_path / "book.pdf")


def setup(book, **kw):
    state = RunState("synthetic", **kw)
    return state, {t.name: t.handler for t in make_tools(book, state)}


def call(tools, name, args):
    return asyncio.run(tools[name](args))


def proposal(entries, confidence=0.9):
    return {"entries": [e.model_dump() for e in entries], "confidence": confidence}


def test_tools_are_named_and_return_json(book):
    state, tools = setup(book)
    assert tuple(tools) == TOOL_NAMES
    out = call(tools, "find_headings", {"items": [{"title": "Second Chapter", "near_page": None, "level": 2}]})
    [row] = json.loads(out["content"][0]["text"])
    assert row["best"]["page"] == 3 and row["status"] == "ok"
    bad = call(tools, "page_lines", {"page": 99})
    assert bad["is_error"] and "page 99 is out of range" in bad["content"][0]["text"]


def test_render_page_returns_an_image_block(book):
    _, tools = setup(book)
    image = call(tools, "render_page", {"page": 2})["content"][1]
    assert image["type"] == "image" and image["mimeType"] == "image/png"
    assert base64.b64decode(image["data"]).startswith(b"\x89PNG")


def test_propose_accepts_a_verified_toc(book):
    state, tools = setup(book)
    out = call(tools, "propose_toc", proposal(good_entries(book)))
    assert out["content"][0]["text"] == "accepted: 4 entries" and not out.get("is_error")
    assert state.result.source == "agent" and state.attempts == 1 and not state.result.needs_human
    again = call(tools, "propose_toc", proposal(good_entries(book)))
    assert again["is_error"] and "already recorded" in again["content"][0]["text"]


def test_propose_gives_one_fix_attempt_then_falls_back(book):
    state, tools = setup(book)
    wrong = good_entries(book)
    wrong[1] = wrong[1].model_copy(update={"level": 1})
    first = call(tools, "propose_toc", proposal(wrong))
    assert first["is_error"] and "this is your last attempt" in first["content"][0]["text"]
    assert "entry 2 'First Chapter': level 1" in first["content"][0]["text"] and state.result is None
    second = call(tools, "propose_toc", proposal(wrong))
    assert second["is_error"] and second["content"][0]["text"].startswith("rejected twice; fallback recorded")
    assert state.result.source == "fallback" and state.result.needs_human and state.attempts == 2


def test_schema_error_counts_as_an_attempt(book):
    state, tools = setup(book)
    out = call(tools, "propose_toc", {"entries": [{"title": "Part One", "level": 0, "page": 2, "line_ids": []}],
                                      "confidence": 0.5})
    text = out["content"][0]["text"]
    assert out["is_error"] and "entry 1 field level" in text and "entry 1 field line_ids" in text
    assert state.attempts == 1 and state.result is None
    assert call(tools, "propose_toc", proposal(good_entries(book)))["content"][0]["text"].startswith("accepted")


def test_fallback_links_the_contents_rows(book):
    result = fallback_toc(book, "synthetic")
    assert [(e.title, e.level, e.page) for e in result.entries] == [
        ("Part One", 1, 2), ("First Chapter", 2, 2), ("Second Chapter", 2, 3), ("3. Third Chapter", 2, 4)]
    assert result.source == "fallback" and result.needs_human and result.unresolved == []


def test_review_file_parses_as_ground_truth(book):
    result = fallback_toc(book, "synthetic")
    result.entries[0].role = "part"
    text = review_text(result, "claude-sonnet-5", "20261004-120000")
    assert "Correct this file, then save it as ground_truth_toc.txt" in text and "# role: part" in text
    assert [(e["title"], e["level"], e["page"] + 1) for e in parse_ground_truth_text(text)] == [
        (e.title, e.level, e.page) for e in result.entries]


def test_hooks_count_calls_and_deny_over_budget(tmp_path):
    state = RunState("synthetic", max_tool_calls=3, max_images=1, log_path=tmp_path / "calls.jsonl")
    hooks = make_hooks(state)
    pre, post = hooks["PreToolUse"][0].hooks[0], hooks["PostToolUse"][0].hooks[0]

    def use(name, n):
        data = {"tool_name": f"mcp__toc__{name}", "tool_input": {"page": 2}, "tool_use_id": f"t{n}"}
        decision = asyncio.run(pre(data, f"t{n}", None))
        asyncio.run(post({**data, "tool_response": [{"type": "text", "text": "x" * 10}]}, f"t{n}", None))
        return decision.get("hookSpecificOutput", {})

    assert use("render_page", 1) == {}
    denied = use("render_page", 2)
    assert denied["permissionDecision"] == "deny" and "image budget" in denied["permissionDecisionReason"]
    assert use("page_lines", 3) == {} and use("page_lines", 4) == {}
    assert "tool budget used up (3 calls)" in use("search", 5)["permissionDecisionReason"]
    assert use("propose_toc", 6) == {}
    assert (state.tool_calls, state.images, state.denied) == (4, 1, 2)
    assert state.calls == {"render_page": 1, "page_lines": 2, "propose_toc": 1}
    log = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(log) == 6 and set(log[0]) == {"t", "tool", "input", "output_chars", "ms"}
    assert log[0]["tool"] == "render_page" and log[0]["ms"] is not None and log[1]["ms"] is None
