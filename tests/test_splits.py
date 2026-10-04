import json

import pytest

from toc_repair.splits import EVALS_DIR, load_splits, split_of


def test_committed_splits_are_disjoint_and_have_cases():
    splits = load_splits()
    assert "lean-startup" in splits["validation"]
    assert splits["train"] == ["shakhsiyya-1"]
    for doc_ids in splits.values():
        for doc_id in doc_ids:
            assert (EVALS_DIR / "cases" / doc_id / "ground_truth_toc.txt").exists(), doc_id
    assert split_of("lean-startup", splits) == "validation"
    assert split_of("nope", splits) is None


def test_overlap_and_unknown_split_rejected(tmp_path):
    p = tmp_path / "splits.json"
    p.write_text(json.dumps({"train": ["a"], "test": ["a"]}))
    with pytest.raises(ValueError, match="both train and test"):
        load_splits(p)
    p.write_text(json.dumps({"dev": ["a"]}))
    with pytest.raises(ValueError, match="unknown split"):
        load_splits(p)
