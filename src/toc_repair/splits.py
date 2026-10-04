import json
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
SPLITS = ("train", "validation", "test")


def load_splits(path: Path = EVALS_DIR / "splits.json") -> dict[str, list[str]]:
    splits = json.loads(Path(path).read_text(encoding="utf-8"))
    unknown = set(splits) - set(SPLITS)
    if unknown:
        raise ValueError(f"unknown split names: {sorted(unknown)}")
    seen: dict[str, str] = {}
    for name in SPLITS:
        for doc_id in splits.setdefault(name, []):
            if doc_id in seen:
                raise ValueError(f"{doc_id} is in both {seen[doc_id]} and {name}")
            seen[doc_id] = name
    return splits


def split_of(doc_id: str, splits: dict[str, list[str]] | None = None) -> str | None:
    splits = splits or load_splits()
    return next((name for name in SPLITS if doc_id in splits[name]), None)


def case_dirs(split: str, cases_dir: Path = EVALS_DIR / "cases") -> list[Path]:
    return [cases_dir / doc_id for doc_id in load_splits()[split]]
