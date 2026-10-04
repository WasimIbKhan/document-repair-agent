import re
import unicodedata
from collections import Counter
from functools import cached_property
from typing import Literal

from pydantic import BaseModel, Field, model_validator

BlockType = Literal[
    "text", "header", "footer", "page_number", "image", "table", "equation",
    "discarded", "page_footnote", "chart", "list", "other",
]
KNOWN_TYPES = set(BlockType.__args__)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _NON_ALNUM.sub(" ", folded).strip()


def contains_words(norm_title: str, norm_text: str) -> bool:
    return bool(norm_title) and f" {norm_title} " in f" {norm_text} "


def to_block_type(value) -> BlockType:
    return value if value in KNOWN_TYPES else "other"


class Page(BaseModel):
    index: int
    width: float
    height: float


class Block(BaseModel):
    id: str
    page: int
    order: int
    type: BlockType
    text: str
    raw_text: str
    bbox: tuple[float, float, float, float]
    font_size: float | None = None
    bold: bool | None = None
    parser_level: int | None = None
    n_chars: int

    @property
    def normalized_text(self) -> str:
        return normalize(self.text)


class OutlineEntry(BaseModel):
    title: str
    level: int
    page: int


class LayoutDoc(BaseModel):
    doc_id: str
    source: dict
    pages: list[Page]
    blocks: list[Block]
    embedded_outline: list[OutlineEntry] = []

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @cached_property
    def _by_id(self) -> dict[str, Block]:
        return {b.id: b for b in self.blocks}

    def blocks_on_page(self, index: int) -> list[Block]:
        return [b for b in self.blocks if b.page == index]

    def block_by_id(self, block_id: str) -> Block | None:
        return self._by_id.get(block_id)

    def font_size_histogram(self) -> dict[float, int]:
        counts: Counter[float] = Counter()
        for b in self.blocks:
            if b.font_size is not None:
                counts[round(b.font_size * 2) / 2] += b.n_chars
        return dict(sorted(counts.items()))


class TocEntry(BaseModel):
    title: str
    level: int = Field(ge=1)
    page: int = Field(ge=0)
    block_id: str


class TocResult(BaseModel):
    entries: list[TocEntry]
    confidence: float = Field(ge=0, le=1)
    source: Literal["heuristic", "agent", "heuristic_fallback"]
    unresolved: list[str] = []
    needs_human: bool = False
    problems: list[str] = []

    @model_validator(mode="after")
    def _structural(self):
        for e in self.entries:
            if e.level < 1 or e.page < 0:
                raise ValueError(f"bad entry {e.title!r}: level={e.level} page={e.page}")
        return self


Role = Literal["part", "chapter", "section", "front_matter", "back_matter"]


class LinkedEntry(BaseModel):
    title: str = Field(min_length=1)
    level: int = Field(ge=1)
    page: int = Field(ge=1, description="1-based PDF page")
    line_ids: list[str] = Field(min_length=1, max_length=6)
    role: Role | None = None


class AgentResult(BaseModel):
    doc_id: str
    entries: list[LinkedEntry]
    unresolved: list[dict] = []
    confidence: float = Field(ge=0, le=1)
    needs_human: bool = False
    source: Literal["agent", "fallback"]
    problems: list[str] = []
