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


# README.md is the directory's own "how this works" doc (docs/knowledge/README.md),
# not domain content — indexing it would let retrieve() match chat messages
# against sentences describing the retrieval mechanism itself (observed: a
# stray "network call" in its prose made "call" spuriously "discriminative"
# for a query like "remind me to call my mom"). Excluded by convention, same
# as a directory listing would skip its own README.
_EXCLUDED_FILENAMES = {"readme.md"}


def _knowledge_files(directory: Path):
    return sorted(f for f in directory.glob("*.md") if f.name.lower() not in _EXCLUDED_FILENAMES)


def _signature(directory: Path) -> frozenset:
    if not directory.is_dir():
        return frozenset()
    sig = set()
    for f in _knowledge_files(directory):
        try:
            sig.add((f.name, f.stat().st_mtime_ns))
        except OSError:
            continue
    return frozenset(sig)


def _build(directory: Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for f in _knowledge_files(directory):
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
    # Index the breadcrumb (H1 > H2 > H3) alongside the body. Without this a
    # section is unfindable by the words in its own title unless the body
    # happens to repeat them — e.g. a chunk headed "Buying Committee Size"
    # scored zero for "buying committee" because the prose below it only ever
    # said "committees". Headings are where authors put the specific term, so
    # they belong in the searchable text; the breadcrumb also carries the
    # parent sections, which is the context a reader would use to find it.
    bm25 = BM25Index([tokenize(f"{c.breadcrumb} {c.text}") for c in chunks])
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
    """Top-k BM25 hits for `query`, above the minimum score floor AND
    containing at least one corpus-discriminative query term (see
    BM25Index.has_discriminative_match) — an unrelated query, or one built
    only from words so common in this corpus that matching them is
    uninformative (e.g. "call", "open", "house" in a real-estate corpus),
    returns an empty list rather than noisy weak matches. Never raises: a
    missing/empty knowledge dir or a bad query just yields no hits."""
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
            ((s, c, i) for i, (s, c) in enumerate(zip(scores, corpus.chunks))
             if s > 0 and s >= min_score
             and corpus.bm25.has_discriminative_match(i, q_tokens)),
            key=lambda triple: triple[0],
            reverse=True,
        )[:k]
        ranked = [(s, c) for s, c, _ in ranked]
        return [Hit(doc=c.doc, heading=c.heading, breadcrumb=c.breadcrumb, text=c.text, score=round(s, 4))
                for s, c in ranked]
    except Exception:
        return []


def hits_to_dicts(hits: list[Hit]) -> list[dict]:
    return [asdict(h) for h in hits]
