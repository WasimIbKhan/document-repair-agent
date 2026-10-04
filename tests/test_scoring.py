from toc_repair.scoring import dense_levels, score

TRUTH = [
    {"title": "Personality", "level": 1, "page": 12},
    {"title": "The Islamic Personality", "level": 2, "page": 15},
    {"title": "Sources of Tafsīr", "level": 1, "page": 233},
]


def test_perfect_prediction_passes():
    r = score([dict(e) for e in TRUTH], TRUTH)
    assert (r["precision"], r["recall"], r["f1"], r["level_accuracy"], r["passed"]) == (1.0, 1.0, 1.0, 1.0, True)


def test_levels_compared_by_nesting_not_raw_numbers():
    shifted = [{**e, "level": e["level"] + 1} for e in TRUTH]
    assert score(shifted, TRUTH)["level_accuracy"] == 1.0
    assert dense_levels([2, 3, 2, 4]) == [1, 2, 1, 3]


def test_truncated_title_wrong_page_wrong_level_and_extra():
    predicted = [
        {"title": "Personality", "level": 1, "page": 12},
        {"title": "The Islamic Personality", "level": 1, "page": 16},
        {"title": "Sources of", "level": 2, "page": 233},
        {"title": "Dear Eric,", "level": 2, "page": 40},
    ]
    r = score(predicted, TRUTH)
    assert r["n_matched"] == 1
    assert [w["title"] for w in r["wrong_page"]] == ["The Islamic Personality"]
    assert [m["title"] for m in r["missed"]] == ["Sources of Tafsīr"]
    assert {e["title"] for e in r["extra"]} == {"Sources of", "Dear Eric,"}
    assert r["precision"] == 0.25 and round(r["recall"], 2) == 0.33 and not r["passed"]
    assert round(r["f1"], 3) == round(2 * 0.25 * (1 / 3) / (0.25 + 1 / 3), 3)


def test_level_mismatch_counted_on_matched_only():
    predicted = [{**TRUTH[0]}, {**TRUTH[1], "level": 1}]
    r = score(predicted, TRUTH[:2])
    assert r["n_matched"] == 2 and r["level_accuracy"] == 0.5
    assert r["wrong_level"][0]["title"] == "The Islamic Personality"


def test_diacritics_and_case_do_not_block_a_match():
    r = score([{"title": "SOURCES OF TAFSIR", "level": 1, "page": 233}], [TRUTH[2]])
    assert r["n_matched"] == 1


def test_empty_inputs():
    assert score([], TRUTH)["recall"] == 0.0 and score([], TRUTH)["f1"] == 0.0
    assert score(TRUTH, [])["precision"] == 0.0
