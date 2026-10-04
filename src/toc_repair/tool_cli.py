import argparse
import json
import sys
import time
from pathlib import Path

from .pdf_tools import list_cases, open_case

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


def call(tool: str, args: argparse.Namespace):
    if tool == "cases":
        return list_cases()
    doc = open_case(args.doc)
    if tool == "summary":
        return doc.summary()
    if tool == "lines":
        return doc.page_lines(args.page, args.detail)
    if tool == "search":
        return doc.search(args.pattern, regex=args.regex)
    if tool == "find":
        return doc.find_heading(args.title, args.near_page, window=args.window)
    if tool == "find-many":
        try:
            raw = sys.stdin.buffer.read() if args.items == "-" else Path(args.items).read_bytes()
            items = json.loads(raw.decode("utf-8-sig"))
        except (OSError, ValueError) as err:
            raise ValueError(f'cannot read a JSON list like [{{"title": "...", "near_page": 12}}] '
                             f"from {args.items!r}: {err}") from None
        return doc.find_headings(items, window=args.window)
    if tool == "contents":
        return doc.contents_view(args.page)
    if tool == "printed":
        return {"printed_page": args.printed, "pdf_page": doc.pdf_page_for(args.printed), **doc.page_map()}
    if tool == "render":
        out = RUNS_DIR / "images" / f"{args.doc}-p{args.page:03d}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(doc.render_page(args.page))
        return {"image": str(out)}
    raise ValueError(f"unknown tool {tool!r}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Call the read-only PDF tools from a shell; every call is logged")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log", default=None, help="append the call to runs/<log>.jsonl")
    sub = ap.add_subparsers(dest="tool", required=True)
    sub.add_parser("cases", parents=[common])
    for name in ("summary", "lines", "search", "find", "find-many", "contents", "printed", "render"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("doc")
        if name in ("lines", "render", "contents"):
            p.add_argument("page", type=int)
        if name == "find-many":
            p.add_argument("items", help="JSON file with [{title, near_page}, ...], or - to read it from stdin")
            p.add_argument("--window", type=int, default=3)
        if name == "lines":
            p.add_argument("--detail", choices=["concise", "detailed"], default="concise")
        if name == "search":
            p.add_argument("pattern")
            p.add_argument("--regex", action="store_true")
        if name == "find":
            p.add_argument("title")
            p.add_argument("near_page", type=int)
            p.add_argument("--window", type=int, default=3)
        if name == "printed":
            p.add_argument("printed", type=int)
    args = ap.parse_args(argv)
    started = time.perf_counter()
    try:
        result, ok = call(args.tool, args), True
    except ValueError as err:
        result, ok = {"error": str(err)}, False
    text = json.dumps(result, ensure_ascii=False)
    if args.log:
        RUNS_DIR.mkdir(exist_ok=True)
        entry = {"tool": args.tool, "args": {k: v for k, v in vars(args).items() if k not in ("tool", "log")},
                 "ok": ok, "chars": len(text), "ms": round((time.perf_counter() - started) * 1000)}
        with (RUNS_DIR / f"{args.log}.jsonl").open("a", encoding="utf-8", errors="replace") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
