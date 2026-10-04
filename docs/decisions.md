# Decisions

One entry per decision, newest last.

<!--
## YYYY-MM-DD: <question>
Options:
Chosen:
Why:
Consequence:
-->

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
