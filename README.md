# toc-repair-agent

Repairs the table of contents of an already-parsed PDF. Input is a layout JSON
(text blocks with bbox, page, text, font size, bold, reading order) built from a
parser's output (MinerU `content_list.json` today) plus the source PDF. A
deterministic heuristic builds a TOC with a confidence score; low confidence
routes the document to a Claude Agent SDK agent, and every result is verified
against the layout before it is trusted.

Design principle: code where code works, model where judgement is needed, model
never trusted without verification.

Built with Claude Code. Architecture, heuristics design, guardrail rules, the
Skill, and the evaluation set are mine.

## Run

    py -3.12 -m venv .venv
    .venv\Scripts\python -m pip install -e ".[dev]"
    .venv\Scripts\toc-repair-build-layout data/raw/<set>/<doc>/book.pdf data/raw/<set>/<doc>/content_list.json -o layout.json
    .venv\Scripts\python -m pytest -q

Step 1 (this commit) ships the schema and `build_layout`. The heuristic,
verifier, agent and eval harness are coming next.

## Eval splits

`evals/splits.json` assigns every case to one split, and the loader rejects
overlaps. Train is what the heuristics and the Skill are tuned on. Validation
checks that tuning generalises. Test is held out and only scored, never tuned
on. The Enlighten books stay gitignored; only their doc ids and hand-written
ground truth are committed.

| split | cases |
|---|---|
| train | shakhsiyya-1 |
| validation | islamic-disposition, islamic-personality-2, political-concepts, political-thoughts, system-of-islam |
| test | lean-startup (more to come) |

## Writing ground truth

Each eval case gets a hand-written `evals/cases/<doc>/ground_truth_toc.txt`,
one heading per line as `Title @ <page>`, where `<page>` is the 1-based page
number a PDF viewer shows (not the printed folio). Nest with 2 spaces per level;
`#` lines and blank lines are ignored.

    Introduction @ 5
    1. What Is Politics? @ 7
      1.1 Internal Affairs @ 7
      1.2 Foreign Policy @ 9
    2. The State @ 14

    .venv\Scripts\toc-repair-gt --init evals/cases/<doc>   # template with doc_id, pdf, page count
    .venv\Scripts\toc-repair-gt evals/cases/<doc>          # validate, resolve to block ids, write ground_truth_toc.json

The converter fuzzy-matches every title to a text block on its page (then the
neighbouring pages) and warns, with the top candidates, on anything it cannot
find, so typos and page slips surface immediately. `--all` runs every case
whose txt has been filled in.
