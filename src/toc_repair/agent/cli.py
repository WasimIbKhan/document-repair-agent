import argparse
import asyncio
import json
import sys

from ..pdf_tools import open_case
from .run import DEFAULT_MODEL, api_key, build_options, run_book
from .tools import RunState, make_tools

COLUMNS = ("doc_id", "final_source", "entries", "verification_attempts", "tool_calls", "images", "turns",
           "wall_time_s", "cost_usd")


def dry_run(doc_id: str, args) -> None:
    doc = open_case(doc_id)
    state = RunState(doc_id, args.max_tool_calls, args.max_images)
    opts = build_options(doc, state, args.model, args.budget_usd, args.max_turns, "<redacted>" if api_key() else None)
    shown = {k: getattr(opts, k) for k in ("model", "tools", "allowed_tools", "setting_sources", "skills",
                                            "permission_mode", "strict_mcp_config", "max_turns", "max_budget_usd",
                                            "cwd")}
    shown["mcp_servers"] = {k: v["type"] for k, v in opts.mcp_servers.items()}
    shown["hooks"] = {k: len(v) for k, v in opts.hooks.items()}
    shown["env"] = {k: ("<redacted>" if "KEY" in k else v) for k, v in opts.env.items()}
    shown["system_prompt"] = opts.system_prompt[:90] + "..."
    print(f"== {doc_id} ({doc.page_count} pages), api key {'found' if api_key() else 'MISSING'}")
    print(json.dumps(shown, indent=1, default=str))
    for t in make_tools(doc, state):
        schema = t.input_schema
        print(f"tool {t.name}({', '.join(schema['properties'])}) required={schema['required']}")
    try:
        from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
        transport = SubprocessCLITransport(prompt="", options=opts)
        transport._cli_path = "claude"
        cmd = [a if len(a) < 120 else a[:60] + "...<trimmed>" for a in transport._build_command()]
        print("cli:", " ".join(cmd))
    except Exception as err:
        print(f"cli: could not build the command ({type(err).__name__}: {err})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="toc-repair-agent", description="Run the TOC linking agent on eval cases")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run books sequentially")
    r.add_argument("doc_ids", nargs="+")
    r.add_argument("--model", default=DEFAULT_MODEL)
    r.add_argument("--budget-usd", type=float, default=1.50)
    r.add_argument("--max-tool-calls", type=int, default=40)
    r.add_argument("--max-images", type=int, default=20)
    r.add_argument("--max-turns", type=int, default=30)
    r.add_argument("--dry-run", action="store_true", help="build options and tools, print them, call no model")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.dry_run:
        for doc_id in args.doc_ids:
            dry_run(doc_id, args)
        return 0
    rows = []
    for doc_id in args.doc_ids:
        stats = asyncio.run(run_book(doc_id, args.model, args.max_tool_calls, args.max_images, args.budget_usd,
                                     args.max_turns))
        rows.append(stats)
        print("  ".join(f"{k}={stats[k]}" for k in COLUMNS) + (f"  error={stats['error']}" if stats["error"] else ""))
    print(f"total: books={len(rows)} agent={sum(s['final_source'] == 'agent' for s in rows)} "
          f"tool_calls={sum(s['tool_calls'] for s in rows)} images={sum(s['images'] for s in rows)} "
          f"wall_time_s={sum(s['wall_time_s'] for s in rows):.1f} cost_usd={sum(s['cost_usd'] or 0 for s in rows):.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
