"""A small retrieval step over a folder of help articles (Markdown).

Each article is split into sections at its headings; sections are scored with
BM25 over words, and the top few go to Claude with an id like `refunds#2` so
every answer can cite where it came from. No vector database is needed for a
help centre of tens to hundreds of articles, and the results are explainable.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

WORD_RE = re.compile(r"[a-z0-9]+")
STOP = set("""a an the and or but if of to in on for with at by from is are was were be been it this that
these those i you we they he she my your our their me us them do does did can could would should will
have has had not no yes so as about into than then there here what which who how when where why""".split())


def tokens(text: str) -> list[str]:
    return [w for w in WORD_RE.findall((text or "").lower()) if w not in STOP and len(w) > 1]


@dataclass
class Section:
    id: str
    title: str
    text: str
    source: str


def split_sections(name: str, markdown: str) -> list[Section]:
    sections, title, buf, n = [], name, [], 0
    for line in markdown.splitlines():
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            if "".join(buf).strip():
                n += 1
                sections.append(Section(f"{name}#{n}", title, "\n".join(buf).strip(), name))
            title, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        n += 1
        sections.append(Section(f"{name}#{n}", title, "\n".join(buf).strip(), name))
    return sections


class KnowledgeBase:
    def __init__(self, sections: list[Section], k1: float = 1.5, b: float = 0.75):
        self.sections = sections
        self.k1, self.b = k1, b
        self._docs = [Counter(tokens(s.title + " " + s.text)) for s in sections]
        self._lens = [sum(d.values()) for d in self._docs]
        self._avg = (sum(self._lens) / len(self._lens)) if self._lens else 0.0
        df = Counter()
        for d in self._docs:
            df.update(d.keys())
        n = len(sections)
        self._idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    @classmethod
    def from_dir(cls, path: str | Path) -> "KnowledgeBase":
        path = Path(path)
        sections: list[Section] = []
        for f in sorted(path.glob("*.md")):
            sections.extend(split_sections(f.stem, f.read_text(encoding="utf-8")))
        return cls(sections)

    def ids(self) -> set[str]:
        return {s.id for s in self.sections}

    def search(self, query: str, k: int = 3) -> list[tuple[Section, float]]:
        q = tokens(query)
        if not q or not self.sections:
            return []
        scored = []
        for s, d, length in zip(self.sections, self._docs, self._lens, strict=True):
            score = 0.0
            for w in q:
                tf = d.get(w, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * length / (self._avg or 1))
                score += self._idf.get(w, 0.0) * tf * (self.k1 + 1) / denom
            if score > 0:
                scored.append((s, score))
        scored.sort(key=lambda x: (-x[1], x[0].id))
        return scored[:k]
