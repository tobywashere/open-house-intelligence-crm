"""Pure-stdlib BM25 (Okapi) scoring — no embeddings, no vector DB, no cloud
calls. Deliberate: the product's offline-first claim depends on retrieval
that needs nothing but the Python standard library.
"""
from __future__ import annotations

import math
import re
import statistics
from collections import Counter

K1 = 1.5
B = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Small English stopword list — enough to keep common function words from
# drowning out the meaningful terms in a short query, not an exhaustive list.
# Includes personal pronouns (i, me, my, we, us, they, ...): a market report
# quoting first-person advisory scripts ("I am structuring this offer...")
# uses these constantly, so without them a completely unrelated first-person
# chat message ("remind me to call my mom") can spuriously match a script
# section on nothing but shared pronouns.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "can",
    "did", "do", "does", "for", "from", "had", "has", "have", "how", "if",
    "in", "into", "is", "it", "its", "itself", "of", "on", "or", "our",
    "ours", "ourselves", "should",
    "so", "that", "the", "their", "theirs", "them", "themselves", "there",
    "these", "this", "those", "to", "was",
    "were", "what", "when", "where", "which", "who", "will", "with",
    "would", "you", "your", "yours", "yourself", "yourselves",
    "i", "me", "my", "mine", "myself",
    "we", "us", "they",
    "he", "him", "his", "himself", "she", "her", "hers", "herself",
    "no", "not", "nor", "than", "then", "too", "very", "just", "only",
    "own", "same", "such", "each", "other", "some", "any", "all", "both",
    "few", "more", "most",
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
        # A query built (partly) from corpus-common words can still
        # accumulate a nonzero BM25 score purely from a single coincidental
        # overlap, even though it shares no genuinely distinguishing
        # vocabulary with any chunk. The median corpus IDF (over unique
        # terms, inclusive) is the discriminativeness threshold: a term at
        # or above it sits in this corpus's rarer half of vocabulary. This
        # is corpus-relative by construction — swap the knowledge dir for a
        # different vertical and the threshold recomputes against that
        # corpus's own vocabulary, not a magic number tuned to one doc set.
        self.median_idf: float = statistics.median(self.idf.values()) if self.idf else 0.0

    def has_discriminative_match(self, doc_index: int, query_tokens: list[str]) -> bool:
        """True iff chunk `doc_index` shows genuine, non-coincidental overlap
        with the query — either (a) at least TWO distinct query terms
        matched, so the hit isn't riding on one word alone, or (b) a single
        matched term whose corpus IDF sits at/above the corpus's own median,
        i.e. a genuinely rare/specific term rather than one common enough in
        this corpus that matching it alone is uninformative. A single
        moderately-common term matching by coincidence (e.g. "open" turning
        up in an unrelated dialogue example) satisfies neither and is
        correctly rejected; a real multi-term query (e.g. "excise" + "tax" +
        "seller") or a single highly specific term (e.g. "vesting") passes."""
        freqs = self.doc_freqs[doc_index]
        matched = [term for term in set(query_tokens) if freqs.get(term)]
        if len(matched) >= 2:
            return True
        if len(matched) == 1:
            return self.idf.get(matched[0], 0.0) >= self.median_idf
        return False

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
