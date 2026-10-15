"""Build, save and load the FAISS index over the policy chunks.

Embeddings are cached under rag/index/<strategy>/ so the Titan calls happen
once per corpus, not once per evaluation run (git-ignored; rebuilt on demand).
"""
from __future__ import annotations

import json
import os

import faiss
import numpy as np

from rag import bedrock
from rag.corpus import Chunk, build_chunks

INDEX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index")


class PolicyIndex:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, strategy: str):
        assert len(chunks) == vectors.shape[0]
        self.chunks = chunks
        self.strategy = strategy
        # Vectors are L2-normalised, so inner product == cosine similarity.
        self.faiss = faiss.IndexFlatIP(vectors.shape[1])
        self.faiss.add(np.ascontiguousarray(vectors, dtype="float32"))

    @classmethod
    def build(cls, strategy: str = "section", force: bool = False) -> "PolicyIndex":
        chunks = build_chunks(strategy)
        d = os.path.join(INDEX_DIR, strategy)
        vec_path = os.path.join(d, "vectors.npy")
        meta_path = os.path.join(d, "chunks.json")
        if not force and os.path.exists(vec_path) and os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                cached = json.load(f)
            same = ([c["chunk_id"] for c in cached] == [c.chunk_id for c in chunks]
                    and all(c["text"] == k.text for c, k in zip(cached, chunks)))
            if same:
                return cls(chunks, np.load(vec_path), strategy)
        print(f"Embedding {len(chunks)} '{strategy}' chunks with Titan V2 "
              f"({sum(c.n_words for c in chunks):,} words)...")
        vectors = bedrock.embed_texts([c.text for c in chunks])
        os.makedirs(d, exist_ok=True)
        np.save(vec_path, vectors)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump([c.as_dict() for c in chunks], f, indent=1)
        return cls(chunks, vectors, strategy)

    def search(self, query: str, k: int = 5,
               doc_ids: set[str] | None = None) -> list[tuple[Chunk, float]]:
        """Top-k chunks for a query, optionally restricted to given doc_ids
        (the 'metadata filter' of Template 4 option C). Filtering is done
        after the search by over-fetching, which is fine at 48 chunks."""
        q = bedrock.embed_texts([query])
        n = len(self.chunks) if doc_ids else k
        scores, idx = self.faiss.search(np.ascontiguousarray(q, dtype="float32"), n)
        out = []
        for s, i in zip(scores[0], idx[0]):
            if i < 0:
                continue
            c = self.chunks[i]
            if doc_ids and c.doc_id not in doc_ids:
                continue
            out.append((c, float(s)))
            if len(out) == k:
                break
        return out
