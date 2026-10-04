# Decisions

One entry per decision, newest last.

<!--
## YYYY-MM-DD: <question>
Options:
Chosen:
Why:
Consequence:
-->

## Background: two years of the same problem (October 2024 to October 2026)

This repo is not the first time I've tried to get book structure right. It's the problem underneath my whole app: if the chapters and sections are wrong, every study plan, summary and lesson built on them is wrong too. Here is what I tried, what I decided each time, and what kept breaking.

**October 2024: first conversions.** I ran about 80 books through marker (an old marker-api wrapper) into markdown. The headings were already wrong in the ways I still see today. In Machiavelli's *The Prince*, the roman-numeral page numbers ("Vi", "Xli") came out as headings. In *The Sealed Nectar*, a Qur'an verse became a heading.

**July 2025: geometry.** First structure code in the app: infer heading levels from bounding-box prominence and clustering. Decision: trust the layout geometry. No eval, so I didn't know how well it worked.

**December 2025: hand-tuned heuristics (V14).** A deterministic pipeline of about 2,500 lines: layout clustering, running-header removal, OCR fixes, TOC-page removal, chapter identification, sequence repair. Tuned by hand across 63 books, with versions up to V14.6. A rule that promoted "Notes to Chapter X" had to be switched off. The code claimed 95% accuracy, but there was no answer key behind that number. I also added the EPUB parser and debugging viewers for position mismatches.

**February to June 2026: the symptoms moved downstream.** I logged V14 on a marketing textbook with no accuracy number, extended the chain to marker, and started telling the plan prompt to keep front and back matter out of milestones. That was a patch on the symptom, not the cause.

**Late June 2026: a repair step after parsing.** I built a separate module to repair the parser's output against the book's own contents page.
- Reading the contents page: text detection found a usable TOC in about 0 of 14 books, while reading the page as an image found 13 of 14.
- First try, an agent per chapter: it did not beat the unrepaired output (one book went 55 to 30), the LLM judge was too noisy to measure with, and it cost about $2.44 a book.
- Decision: an agent only to read the contents page (under a cent a book), deterministic code for everything else. A fix to the page-offset matcher raised mean coverage from 0.68 to 0.80.

**July 2026: wired into ingestion, and parser churn.** The repair now runs on every parse and fails safe. The primary parser switched three times between MinerU and marker. A split chapter-label bug left 44% of the Muqaddimah's text on the wrong nodes.

**August to September 2026: more downstream symptoms.** Plans ended at heading 80 until I raised the cap. EPUBs whose headings were styled paragraphs matched 0 of 146 contents entries until I rewrote them as real headings (then 146 of 146).

**End of September 2026: the Lean Startup bug.** MinerU filed chapter openers as page headers and page numbers. The repair only searched body text, so "Introduction" swallowed 99% of the book, and extracting it cost $0.79 instead of about $0.05. That is why this repo exists.

**What kept failing, every time:**
- The parser's heading levels can't be trusted, and every fix was per book: rules tuned on 63 books, three parser switches, patch after patch.
- Matching titles against the parser's text, because that text is itself broken.
- Nesting by level alone, so one wrong level swallows everything after it.
- No real measurement. A 95% claim with no answer key, a noisy LLM judge, and no held-out test books until this repo.

The decisions below are what I'm doing differently.

## 2026-09-28: Which documents do we evaluate on?

Options: public-domain PDFs only, as the brief says, or the real broken books from Enlighten.

Chosen: the Enlighten books, gitignored, for training and validation. Publicly available books get added for testing. Shakhsiyya is the training book. Lean Startup is the first test book, and I'll bring a whole host of other books for testing.

Why: these books have the most broken documents, and that is the problem I'm actually solving. Lean Startup is the book that exposed the bug in the app.

Consequence: the books never go in the repo, only their doc ids and the ground truth. Lean Startup was studied in detail before it became a test book, so it's the motivating case, not a clean held-out number.

## 2026-10-04: How was the Shakhsiyya ground truth made?

Options: hand-write every entry one by one, or have a model transcribe the contents pages and verify it myself.

Chosen: I pasted a picture of each contents page into Sonnet, had it write the TOC, and then verified it as a human.

Why: hand-writing them one by one wasn't realistic. It shows it is possible, with relatively high accuracy (my human guess was 98%), to extract a correct TOC from the contents pages. But not all of it was correct.

What I checked: the pages were off, so I applied an offset of 12 and confirmed it held across the whole PDF, at the beginning, the end and the middle. I verified each heading. Only three were wrong: Al-Qadr, Al-Qadā' and Al-Qadā' wa'l-Qadar were indented one level more than they should be, and I fixed them.

Consequence: the answer key is model-transcribed and human-verified, not hand-written, and it says so in the file. An independent check against the PDF afterwards found 72 of 72 titles and pages correct, and every level matching the contents page indentation.

## 2026-10-04: Levels follow the author, not the reader

Options: nest entries by what makes sense to a reader, or exactly as the author laid them out.

Chosen: exactly as the author laid them out.

Why: as a human we think it's appropriate that those three Qadar entries should be more indented than the rest, but that's not the author's intent, that's just the observer's intent, and an agent assumed the same thing. We must stick true to what the author intended and not the human. Perhaps the LLM's training affected the indentation, and that's why it didn't follow the other patterns. "Sources of Tafsīr" is the same case the other way round: by meaning it belongs under "The Islamic Disciplines", but the author puts it at the top level.

Consequence: the agent will likely share the same bias, so the Skill has to tell it this, and levels can be checked against the contents page indentation.

## 2026-10-04: What do we verify a heading against?

Options: (A) the PDF's own text inside the block's position, or (B) a looser partial match against the parser's text.

Chosen: A.

Why: the parser drops words from headings. In Shakhsiyya it dropped the italic transliterated words from 10 of 72 headings and one heading entirely, so matching against the parser's text rejects true headings. Every time we matched titles against parser text we patched it for some books, but not books like these. Honestly, the only way I feel this working is if an agent itself checks against a TOC generated from the contents pages.

Consequence: deterministic checks only over evidence that can't be broken: the PDF text layer, the page geometry, the printed page numbers. The agent where evidence has to be interpreted. Verification needs the PDF, not just the layout.

## 2026-10-04: Where does the reference TOC come from?

Options: rebuild it from the body headings, or read it from the book's contents pages.

Chosen: the contents pages, read by a model, are the reference. The agent's job is to find each entry in the body.

Why: reading the contents pages was the part that worked, both in my earlier experiments and for Shakhsiyya. The failures came later, when lining entries up with the parser's output.

Books without a usable contents page: we'll deal with this if it ever comes up. Most books, I'd say 99%, have structure.

## 2026-10-04: Cost is part of the R&D

Options: accept whatever the agent costs, or treat finding cheaper ways as part of the research.

Chosen: finding cheaper ways is part of the R&D.

Why: my mistake before was that I was too lazy and didn't want to find cheaper alternative ways to do this. My earlier agent cost about $2.44 a book, mostly from re-sending page images every turn, while reading the contents pages cost under a cent. It's all to do with methodology and mechanisms. Finding cheaper ways to do this is something we hope to identify over time with experiments.

Consequence: every run logs its cost, and cheaper mechanisms get compared on the evals like any other change.

## 2026-10-04: What does a TOC entry point to?

Options: (A) a block in the parser's output, as the brief says, or (B) a place in the PDF itself: the page and the line in the PDF's own text.

Chosen: B.

Why: the parser loses headings. In Shakhsiyya it dropped "The Islamic Personality" on page 16 completely, so there is no block to point to, and with block ids that heading could never be cited however good the agent is. A place in the PDF always exists. Claude can construct the TOC to near 100%, but unless it's linked to the document it means nothing, and B is what makes the link possible every time. It also removes the problem of headings split across blocks, like "Part One" and "VISION" in Lean Startup, because in the PDF that's one line.

Consequence: the schema's TOC entry changes from a block id to a page and a PDF line. When I port this into Enlighten, a place in the PDF maps to the first parser item at or after it by page and position, which works even when the parser's text is broken.

## 2026-10-04: Agent-first, not heuristics-first

Options: the brief's plan (deterministic heuristics handle most books, a confidence score routes the rest to an agent), or an agent first for every book.

Chosen: agent-first, on two conditions. The deterministic parts become the agent's tools and its checker, not a gate in front of it. And the scorer comes first, so every run is measured.

Why: we did so well with Shakhsiyya because Sonnet's vision read the contents pages at 95%+ and I as a human verified it. 95% in a complex document like that was acceptable. The reading was the model's work; the linking was mine: going to each page, finding the heading, checking it. That linking is exactly what an agent with good tools can do, in one mechanism for every book, instead of another pile of per-book rules like the last two years.

The checker stays outside the model: every linked heading must be at the cited place in the PDF's own text, and levels must agree with the contents page indentation, which is the mistake I fixed by hand.

Consequence: the heuristic router in the brief is no longer a prerequisite. A fast path that skips the agent for easy books becomes a cost experiment later: measure what the agent costs per book, then test whether a shortcut saves money without losing accuracy.

## 2026-10-04: Tool design

Options: many small tools that wrap the PDF, or a few tools that each do a whole job.

Chosen: a few tools that each do a whole job, following Anthropic's guidance on writing tools for agents. If we add any more tools, we'll be careful. The agent cites the line ids the tools give it, like `p234-l02`, never coordinates, and two ids when a heading is split over two lines, like "Part One" and "VISION".

Why: the agent can't invent a position it was never shown, and every tool reads the PDF's own text, not the parser's. On page 234 of Shakhsiyya the PDF says "Sources of Tafsīr" in full where the parser had "Sources of".

## 2026-10-04: Text first, vision for the harder books

Options: read every contents page and every heading from page images, or use the PDF's text layer and only look at images when the text can't do it.

Chosen: text first. The contents page is the source of truth and connecting it to the body is the hard part. Vision is for the more difficult books: when the text layer is missing or garbled, as in a scanned book, or when the text can't settle which line is the heading.

Why: the text layer already gives every line's size, boldness and position, which is enough for most books, and it costs almost nothing next to images. It's part of finding cheaper ways to do this.

## 2026-10-04: Page image budget

Chosen: keep it low: at most 20 page images per book for now. I suppose you can't have more than 20 heading 1s in most books.

Consequence: every run logs how many images it used, so the evals show whether the cap is ever reached. Images stay in the conversation and are paid for again on every later turn, so the real cost of an image is higher than one call.
