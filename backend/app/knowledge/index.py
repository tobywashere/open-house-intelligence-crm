"""Lazy, in-memory, mtime-invalidated BM25 index over the knowledge directory.

Built on first use and cached; rebuilt automatically whenever any source
`.md` file's mtime (or the set of files) changes, so an operator can edit or
drop in a new doc without restarting the server. All of this runs as plain
in-process file I/O — it must NEVER be called from inside a `with
get_conn()` block (see backend/app/db.py's docstring): that holds an
exclusive SQLite write lock and file/indexing work has no business blocking
other writers behind it.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from pathlib import Path

from .bm25 import BM25Index, tokenize
from .chunking import Chunk, chunk_markdown

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

DEFAULT_KNOWLEDGE_DIR = "docs/knowledge"
DEFAULT_TOP_K = 3
# BM25 scores for a genuinely matching section on this corpus's short queries
# land well above 3 (multi-term overlap on a specific heading); scores for an
# off-topic query (no shared vocabulary at all) are 0. 1.0 sits comfortably
# above float noise from a single incidental stopword-adjacent term match
# while well below any real multi-term hit — see backend/tests/test_knowledge.py
# and the sample retrieval output in docs/superpowers/rag-impl-report.md.
DEFAULT_MIN_SCORE = 1.0


@dataclass
class Hit:
    doc: str
    heading: str
    breadcrumb: str
    text: str
    score: float


def knowledge_dir() -> Path:
    raw = os.environ.get("KNOWLEDGE_DIR", "").strip() or DEFAULT_KNOWLEDGE_DIR
    p = Path(raw)
    return p if p.is_absolute() else (REPO_ROOT / p)


def top_k_default() -> int:
    try:
        return int(os.environ.get("KNOWLEDGE_TOP_K", "").strip() or DEFAULT_TOP_K)
    except ValueError:
        return DEFAULT_TOP_K


def min_score_default() -> float:
    try:
        return float(os.environ.get("KNOWLEDGE_MIN_SCORE", "").strip() or DEFAULT_MIN_SCORE)
    except ValueError:
        return DEFAULT_MIN_SCORE


class _Corpus:
    __slots__ = ("chunks", "bm25", "signature")

    def __init__(self, chunks: list[Chunk], bm25: BM25Index, signature: frozenset):
        self.chunks = chunks
        self.bm25 = bm25
        self.signature = signature


_cache: dict[str, _Corpus] = {}


def _signature(directory: Path) -> frozenset:
    if not directory.is_dir():
        return frozenset()
    sig = set()
    for f in directory.glob("*.md"):
        try:
            sig.add((f.name, f.stat().st_mtime_ns))
        except OSError:
            continue
    return frozenset(sig)


def _build(directory: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for f in sorted(directory.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        chunks.extend(chunk_markdown(text, doc=f.name))
    return chunks


def get_corpus(directory: Path | None = None) -> _Corpus:
    directory = directory or knowledge_dir()
    key = str(directory)
    sig = _signature(directory)
    cached = _cache.get(key)
    if cached is not None and cached.signature == sig:
        return cached
    chunks = _build(directory) if sig else []
    bm25 = BM25Index([tokenize(c.text) for c in chunks])
    corpus = _Corpus(chunks=chunks, bm25=bm25, signature=sig)
    _cache[key] = corpus
    return corpus


def retrieve(
    query: str,
    k: int | None = None,
    *,
    directory: Path | None = None,
    min_score: float | None = None,
) -> list[Hit]:
    """Top-k BM25 hits for `query`, above the minimum score floor — an
    unrelated query returns an empty list rather than noisy weak matches.
    Never raises: a missing/empty knowledge dir or a bad query just yields
    no hits."""
    query = (query or "").strip()
    if not query:
        return []
    k = k if k is not None else top_k_default()
    min_score = min_score if min_score is not None else min_score_default()
    try:
        corpus = get_corpus(directory)
        if not corpus.chunks:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        scores = corpus.bm25.score(q_tokens)
        ranked = sorted(
            ((s, c) for s, c in zip(scores, corpus.chunks) if s > 0 and s >= min_score),
            key=lambda pair: pair[0],
            reverse=True,
        )[:k]
        return [Hit(doc=c.doc, heading=c.heading, breadcrumb=c.breadcrumb, text=c.text, score=round(s, 4))
                for s, c in ranked]
    except Exception:
        return []


def hits_to_dicts(hits: list[Hit]) -> list[dict]:
    return [asdict(h) for h in hits]
