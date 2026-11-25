"""Splits documents into chunks. Each chunk keeps its own text and a copy of the document's metadata."""
from dataclasses import dataclass

from rag.loader import Doc


@dataclass
class Chunk:
    id: str            # "<doc id>#<number>", e.g. "of-210-city#2"
    doc_id: str
    text: str          # the original text, exactly as it will be shown to the model
    meta: dict


def chunk_fixed(doc: Doc, size: int = 350, overlap: int = 50) -> list[Chunk]:
    """Baseline: cut every `size` characters, with `overlap` characters repeated between neighbours."""
    chunks = []
    step = size - overlap
    for start in range(0, max(len(doc.text), 1), step):
        piece = doc.text[start:start + size]
        if piece.strip():
            chunks.append(Chunk(f"{doc.id}#{len(chunks)}", doc.id, piece, dict(doc.meta)))
        if start + size >= len(doc.text):
            break
    return chunks


def chunk_by_headings(doc: Doc) -> list[Chunk]:
    """TODO (step 2): one chunk per "## " section.

    - Every chunk starts with a heading path line: "<document title> › <section heading>".
      The text before the first "## " goes in a chunk whose path line is just the title.
    - Skip the "# " title line itself (the path line already carries the title).
    - Keep each section whole, so a table stays together with its header row.
    - Copy the document metadata and add meta["section"] = the section heading.
    - Ids: f"{doc.id}#{n}", numbered from 0.
    """
    chunks: list[Chunk] = []

    def add_chunk(section_heading: str | None, body: str) -> None:
        body = body.strip()
        if not body:
            return

        path = doc.title if section_heading is None else f"{doc.title} › {section_heading.strip()}"
        meta = dict(doc.meta)
        if section_heading is not None:
            meta["section"] = section_heading.strip()

        chunks.append(Chunk(
            id=f"{doc.id}#{len(chunks)}",
            doc_id=doc.id,
            text=f"{path}\n{body}",
            meta=meta,
        ))

    lines = doc.text.splitlines()
    preamble_lines: list[str] = []
    section_heading: str | None = None
    section_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if section_heading is None:
                add_chunk(None, "\n".join(preamble_lines))
            else:
                add_chunk(section_heading, "\n".join(section_lines))

            section_heading = stripped[3:].strip()
            section_lines = []
        elif section_heading is None:
            if stripped.startswith("# "):
                continue
            preamble_lines.append(line)
        else:
            section_lines.append(line)

    if section_heading is None:
        add_chunk(None, "\n".join(preamble_lines))
    else:
        add_chunk(section_heading, "\n".join(section_lines))

    return chunks
