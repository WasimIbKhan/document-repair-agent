import pytest
from test_pdf_tools import BODY, make_pdf

from toc_repair.schema import LinkedEntry
from toc_repair.verify import verify

HEADER = (40, 40, "A Synthetic Book", 8)


def body_page(headings, folio):
    return [HEADER, *[(40, 100 + 40 * i, t, s) for i, (t, s) in enumerate(headings)],
            *[(40, 220 + 16 * i, line, 11) for i, line in enumerate(BODY)], (195, 585, str(folio), 9)]


def make_book(path):
    contents = [(150, 60, "Contents", 14),
                (40, 100, "Part One", 11), (350, 100, "1", 11),
                (56, 120, "First Chapter", 11), (350, 120, "1", 11),
                (56, 140, "Second Chapter", 11), (350, 140, "2", 11),
                (56, 160, "3. Third Chapter", 11), (350, 160, "3", 11)]
    return make_pdf(path, [contents, body_page([("Part One", 20), ("First Chapter", 16)], 1),
                           body_page([("Second Chapter", 16)], 2), body_page([("Chapter 3 Third Chapter", 16)], 3)])


@pytest.fixture()
def book(tmp_path):
    return make_book(tmp_path / "book.pdf")


def line_id(doc, page, text):
    return next(ln["id"] for ln in doc.page_lines(page) if ln["text"] == text)


def good_entries(doc):
    return [LinkedEntry(title="Part One", level=1, page=2, line_ids=[line_id(doc, 2, "Part One")]),
            LinkedEntry(title="First Chapter", level=2, page=2, line_ids=[line_id(doc, 2, "First Chapter")]),
            LinkedEntry(title="Second Chapter", level=2, page=3, line_ids=[line_id(doc, 3, "Second Chapter")]),
            LinkedEntry(title="3. Third Chapter", level=2, page=4,
                        line_ids=[line_id(doc, 4, "Chapter 3 Third Chapter")])]


def test_correct_entries_pass_including_label_stripping(book):
    assert verify(book, good_entries(book)) == []


def test_cited_text_must_match_title(book):
    entries = good_entries(book)
    body = line_id(book, 3, BODY[0])
    entries[2] = entries[2].model_copy(update={"line_ids": [body]})
    [err] = verify(book, entries)
    assert err.startswith(f"entry 3 'Second Chapter': lines {body} read '{BODY[0]}' (score ")
    assert err.endswith("cite the line(s) that hold exactly this heading")


def test_line_ids_must_exist_sit_on_the_page_and_be_adjacent(book):
    entries = good_entries(book)
    entries[0] = entries[0].model_copy(update={"line_ids": ["p099-l00"]})
    entries[1] = entries[1].model_copy(update={"page": 3})
    entries[2] = entries[2].model_copy(update={"line_ids": [line_id(book, 3, BODY[0]), line_id(book, 3, BODY[2])]})
    errors = verify(book, entries)
    assert "entry 1 'Part One': line id 'p099-l00' does not exist" in errors[0]
    assert "entry 2 'First Chapter': line p002-" in errors[1] and "is on page 2, not page 3" in errors[1]
    assert "entry 3 'Second Chapter'" in errors[2] and "are not adjacent" in errors[2]


def test_levels_and_pages(book):
    entries = good_entries(book)
    first = entries[0].model_copy(update={"level": 2})
    jump = entries[2].model_copy(update={"level": 4})
    errors = verify(book, [first, entries[1], jump])
    assert any("entry 1 'Part One': the first entry must be level 1" in e for e in errors)
    assert any("entry 3 'Second Chapter': level 4 jumps more than one deeper than entry 2" in e for e in errors)
    errors = verify(book, [entries[0], entries[2], entries[1]])
    assert any("entry 3 'First Chapter': page 2 comes before entry 2 'Second Chapter' on page 3" in e
               for e in errors)


def test_levels_follow_contents_indentation(book):
    entries = good_entries(book)
    entries[1] = entries[1].model_copy(update={"level": 1})
    [err] = verify(book, entries)
    assert err.startswith("entry 2 'First Chapter': level 1, but the contents page (PDF page 1) indents "
                          "'First Chapter' at level 2")


def test_empty_proposal_is_an_error(book):
    assert verify(book, [])[0].startswith("no entries")
