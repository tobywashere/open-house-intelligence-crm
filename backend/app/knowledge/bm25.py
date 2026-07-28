"""Pure-stdlib BM25 (Okapi) scoring — no embeddings, no vector DB, no cloud
calls. Deliberate: the product's offline-first claim depends on retrieval
that needs nothing but the Python standard library.
"""
from __future__ import annotations

import math
import re
from collections import Counter

K1 = 1.5
B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Small English stopword list — enough to keep common function words from
# drowning out the meaningful terms in a short query, not an exhaustive list.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "did", "do", "does", "for", "from", "had", "has", "have", "how", "if",
    "in", "into", "is", "it", "its", "of", "on", "or", "our", "should",
    "so", "that", "the", "their", "there", "these", "this", "to", "was",
    "were", "what", "when", "where", "which", "who", "will", "with",
    "would", "you", "your",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class BM25Index:
    """Scores an already-tokenized corpus. `docs` is a list of token lists,
    one per chunk, in the same order the caller's chunk list is in."""

    def __init__(self, tokenized_docs: list[list[str]]):
        self.N = len(tokenized_docs)
        self.doc_freqs: list[Counter] = [Counter(d) for d in tokenized_docs]
        self.doc_lens = [len(d) for d in tokenized_docs]
        self.avgdl = (sum(self.doc_lens) / self.N) if self.N else 0.0
        df: Counter = Counter()
        for freqs in self.doc_freqs:
            for term in freqs:
                df[term] += 1
        self.idf: dict[str, float] = {
            term: math.log(1 + (self.N - n + 0.5) / (n + 0.5))
            for term, n in df.items()
        }

    def score(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.N
        if not self.N:
            return scores
        for i in range(self.N):
            freqs = self.doc_freqs[i]
            dl = self.doc_lens[i] or 1
            total = 0.0
            for term in query_tokens:
                f = freqs.get(term)
                if not f:
                    continue
                idf = self.idf.get(term, 0.0)
                denom = f + K1 * (1 - B + B * dl / self.avgdl if self.avgdl else 1)
                total += idf * (f * (K1 + 1)) / denom
            scores[i] = total
        return scores
