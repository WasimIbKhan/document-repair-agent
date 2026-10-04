from rapidfuzz import fuzz

from .schema import LayoutDoc, normalize

TITLE_THRESHOLD = 90
PASS_PRECISION = 0.9
PASS_RECALL = 0.9


def dense_levels(levels: list[int]) -> list[int]:
    # Compare nesting shape, not raw numbers: a parser that uses 2 for chapters and 3 for
    # sections nests the same way as an answer key that uses 1 and 2.
    rank = {level: i + 1 for i, level in enumerate(sorted(set(levels)))}
    return [rank[level] for level in levels]


def score(predicted: list[dict], truth: list[dict], title_threshold: float = TITLE_THRESHOLD) -> dict:
    p_levels = dense_levels([e["level"] for e in predicted])
    t_levels = dense_levels([e["level"] for e in truth])
    p_titles = [normalize(e["title"]) for e in predicted]
    used: set[int] = set()
    matched, missed, wrong_page, wrong_level = [], [], [], []

    for ti, t in enumerate(truth):
        title = normalize(t["title"])
        best = None
        for pi, p in enumerate(predicted):
            if pi in used:
                continue
            s = fuzz.ratio(title, p_titles[pi])
            if s < title_threshold:
                continue
            key = (p["page"] == t["page"], s, -abs(p["page"] - t["page"]))
            if best is None or key > best[0]:
                best = (key, pi)
        if best is None:
            missed.append(t)
            continue
        pi = best[1]
        used.add(pi)
        p = predicted[pi]
        if p["page"] != t["page"]:
            wrong_page.append({"title": t["title"], "truth_page": t["page"], "predicted_page": p["page"]})
            continue
        matched.append((ti, pi))
        if p_levels[pi] != t_levels[ti]:
            wrong_level.append({"title": t["title"], "truth_level": t_levels[ti], "predicted_level": p_levels[pi]})

    extra = [predicted[i] for i in range(len(predicted)) if i not in used]
    n = len(matched)
    precision = n / len(predicted) if predicted else 0.0
    recall = n / len(truth) if truth else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "level_accuracy": (n - len(wrong_level)) / n if n else 0.0,
        "passed": precision >= PASS_PRECISION and recall >= PASS_RECALL,
        "n_predicted": len(predicted),
        "n_truth": len(truth),
        "n_matched": n,
        "missed": missed,
        "wrong_page": wrong_page,
        "wrong_level": wrong_level,
        "extra": extra,
    }


def parser_toc(layout: LayoutDoc) -> list[dict]:
    return [{"title": b.text, "level": b.parser_level, "page": b.page}
            for b in layout.blocks if b.parser_level and b.text]
