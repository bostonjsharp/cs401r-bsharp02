"""Load the policy corpus and split it into retrievable chunks.

Two chunking strategies are implemented so they can be compared:

  section  one chunk per markdown heading (## or ###), the document's own
           structure. Every chunk is a self-contained policy clause with its
           section number in the heading, which is what an offer must cite.
  fixed    sliding window of N words with M words of overlap, ignoring
           structure. The textbook default; included so the choice between
           the two is measured, not assumed.

Every chunk carries metadata: doc_id (POL-LOY-011 etc.), the file, the
heading path, and a stable chunk_id like "POL-LOY-011#4" that the examples
file and the report can cite.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, asdict

CORPUS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "northstar-policy-docs")

_DOC_ID_RE = re.compile(r"\*\*Document ID:\*\*\s*([A-Z]+-[A-Z]+-\d+)")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")


@dataclass
class Chunk:
    chunk_id: str      # "POL-LOY-011#4.1"
    doc_id: str        # "POL-LOY-011"
    source: str        # "loyalty-program-terms.md"
    title: str         # document title (the H1)
    heading: str       # "4.1 Annual Gear Credit" (or "4 > 4.1 ..." path for fixed)
    text: str          # the chunk body, heading included so the embedding sees it
    n_words: int

    def as_dict(self) -> dict:
        return asdict(self)


def load_documents(corpus_dir: str = CORPUS_DIR) -> list[dict]:
    docs = []
    for name in sorted(os.listdir(corpus_dir)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(corpus_dir, name), encoding="utf-8") as f:
            text = f.read()
        m = _DOC_ID_RE.search(text)
        title_m = re.search(r"^#\s+(.*)$", text, re.M)
        docs.append({
            "source": name,
            "doc_id": m.group(1) if m else name,
            "title": title_m.group(1).strip() if title_m else name,
            "text": text,
        })
    return docs


def _sections(doc: dict) -> list[tuple[str, str]]:
    """Split one document at ## / ### headings. Returns (heading, body) pairs.
    The H1 title block (document ID, effective date) becomes a 'preamble'
    section so the metadata is retrievable too."""
    lines = doc["text"].splitlines()
    sections, heading, buf = [], "preamble", []
    for line in lines:
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) in (2, 3):
            if buf:
                sections.append((heading, "\n".join(buf).strip()))
            heading, buf = m.group(2).strip(), []
        elif m and len(m.group(1)) == 1:
            continue  # H1 title: carried in Chunk.title instead
        else:
            buf.append(line)
    if buf:
        sections.append((heading, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if b and b != "---"]


def chunk_by_section(docs: list[dict] | None = None) -> list[Chunk]:
    docs = docs or load_documents()
    chunks = []
    for doc in docs:
        for heading, body in _sections(doc):
            num = re.match(r"^(\d+(?:\.\d+)*)\.?\s", heading)
            sec_id = num.group(1) if num else re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
            text = f"{doc['title']} ({doc['doc_id']}) — {heading}\n{body}"
            chunks.append(Chunk(
                chunk_id=f"{doc['doc_id']}#{sec_id}", doc_id=doc["doc_id"],
                source=doc["source"], title=doc["title"], heading=heading,
                text=text, n_words=len(body.split()),
            ))
    return chunks


def chunk_fixed(docs: list[dict] | None = None, size: int = 150, overlap: int = 30) -> list[Chunk]:
    docs = docs or load_documents()
    chunks = []
    for doc in docs:
        words = re.sub(r"^#\s+.*$", "", doc["text"], count=1, flags=re.M).split()
        step = max(size - overlap, 1)
        for i, start in enumerate(range(0, max(len(words) - overlap, 1), step)):
            body = " ".join(words[start:start + size])
            if not body:
                continue
            chunks.append(Chunk(
                chunk_id=f"{doc['doc_id']}#w{i}", doc_id=doc["doc_id"],
                source=doc["source"], title=doc["title"],
                heading=f"words {start}-{start + size}",
                text=f"{doc['title']} ({doc['doc_id']})\n{body}", n_words=len(body.split()),
            ))
    return chunks


def build_chunks(strategy: str = "section", **kw) -> list[Chunk]:
    if strategy == "section":
        return chunk_by_section()
    if strategy == "fixed":
        return chunk_fixed(**kw)
    raise ValueError(f"unknown chunking strategy {strategy!r}")


if __name__ == "__main__":
    import sys
    strategy = sys.argv[1] if len(sys.argv) > 1 else "section"
    cs = build_chunks(strategy)
    print(f"{strategy}: {len(cs)} chunks, {sum(c.n_words for c in cs):,} words, "
          f"min {min(c.n_words for c in cs)} / median {sorted(c.n_words for c in cs)[len(cs)//2]} / "
          f"max {max(c.n_words for c in cs)} words per chunk")
    for c in cs:
        print(f"  {c.chunk_id:22s} {c.n_words:4d}w  {c.heading}")
