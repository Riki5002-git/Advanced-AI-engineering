"""The company's knowledge base: the documents from lesson 2, with a simple keyword search.
The search itself is not the topic of this lesson, so it is deliberately short."""
import math
import re
from dataclasses import dataclass
from pathlib import Path

from agent.config import ROOT

PREFIXES = ("וה", "שה", "וב", "ול", "ה", "ו", "ב", "ל", "מ", "ש", "כ")


@dataclass
class Section:
    doc_id: str
    doc_title: str
    heading: str
    text: str


def _front_matter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    _, header, body = raw.split("---", 2)
    meta = {}
    for line in header.strip().splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta, body.strip()


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z0-9\-]+|[֐-׿]+", text.lower()):
        if len(w) > 3 and w[0] in "והבלמשכ":
            for p in PREFIXES:
                if w.startswith(p) and len(w) - len(p) >= 3:
                    w = w[len(p):]
                    break
        out.append(w)
    return out


class KnowledgeBase:
    def __init__(self, folder: Path = ROOT / "data"):
        self.docs: dict[str, tuple[str, str]] = {}      # doc_id -> (title, full text)
        self.sections: list[Section] = []
        for path in sorted(folder.rglob("*.md")):
            meta, body = _front_matter(path.read_text(encoding="utf-8"))
            if meta.get("status") == "superseded":
                continue
            title = meta.get("title", path.stem)
            self.docs[path.stem] = (title, body)
            for part in re.split(r"\n(?=## )", body):
                heading = part.splitlines()[0].lstrip("# ").strip()
                self.sections.append(Section(path.stem, title, heading, part.strip()))
        n = len(self.sections)
        df: dict[str, int] = {}
        self._toks = []
        for s in self.sections:
            t = set(tokens(s.doc_title + " " + s.text))
            self._toks.append(t)
            for w in t:
                df[w] = df.get(w, 0) + 1
        self.idf = {w: math.log(1 + n / c) for w, c in df.items()}

    def search_sections(self, query: str, k: int = 3) -> list[Section]:
        q = set(tokens(query))
        scored = [(sum(self.idf.get(w, 0) for w in q & t), i) for i, t in enumerate(self._toks)]
        scored.sort(reverse=True)
        return [self.sections[i] for score, i in scored[:k] if score > 0]

    def full_doc(self, doc_id: str) -> str:
        title, body = self.docs[doc_id]
        return body
