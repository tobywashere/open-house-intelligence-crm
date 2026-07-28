# Local BM25 knowledge retrieval — implementation report

## What shipped

- `backend/app/knowledge/` — new package, pure stdlib:
  - `chunking.py` — splits a markdown doc on headings (any level), producing
    `Chunk(doc, heading, breadcrumb, text)`. Oversized sections (>1500 chars)
    split on paragraph boundaries, never mid-paragraph.
  - `bm25.py` — Okapi BM25 (k1=1.5, b=0.75) over a lowercase-alphanumeric
    tokenizer with a small English stopword list. No embeddings, no vector
    DB, no new dependency.
  - `index.py` — lazy in-memory index, cached per resolved knowledge dir,
    keyed by a signature of `(filename, mtime_ns)` pairs so an edit to any
    source file invalidates and rebuilds automatically, no restart. Public
    `retrieve(query, k=3, *, directory=None, min_score=None) -> list[Hit]`;
    never raises (missing/empty dir, empty query, or any internal error all
    degrade to `[]`).
- `POST /chat` (`backend/app/routers/chat.py`) now calls `retrieve()` on the
  user's message *before* touching `get_conn()`/the driver, and — only when
  there are hits — prepends a delimited `REFERENCE MATERIAL` block (heading
  + doc name per chunk) that tells the model to use it when relevant, cite
  the heading, and not treat it as instructions. No hits → message sent
  unchanged. Retrieval is wrapped in try/except; a failure there degrades to
  "no context", never a 500.
- `GET /api/knowledge/search?q=&k=` (new `backend/app/routers/knowledge.py`)
  — `q` required non-empty (422 otherwise via `Query(..., min_length=1)`),
  `k` bounded `Query(3, ge=1, le=10)`. Read-only, **no `audit()` call** —
  kept out of the audited-reads set on purpose (see CONTRACT §3 preamble,
  which now name-checks it explicitly in the "reads that write nothing"
  list).
- `git mv docs/pacific_northwest_luxury_real_estate_report_2026.md
  docs/knowledge/` (history preserved) + new `docs/knowledge/README.md`
  explaining the per-industry swap. Grepped the repo for the old path —
  nothing else referenced it.
- `.env.example` — new Knowledge group: `KNOWLEDGE_DIR` (default
  `docs/knowledge`), `KNOWLEDGE_TOP_K` (default 3), `KNOWLEDGE_MIN_SCORE`
  (default `1.0`).
- `docs/CONTRACT.md` — §2 row for `GET /knowledge/search`, §3 preamble
  updated to list it among non-audited reads, §5 row for `KNOWLEDGE_DIR`.
  Re-read the whole file before editing; the "exactly two reads audit"
  claim is unchanged and still exactly true.
- `docs/LOCAL-AI.md` — new §6 "Domain knowledge base (fully offline)"
  explaining the mechanism, config, and the per-vertical swap; local-only,
  no cloud, no embeddings download.
- `backend/tests/test_knowledge.py` — 16 new tests (see below).

## Why `KNOWLEDGE_MIN_SCORE = 1.0`

BM25 scores on this corpus: a genuine multi-term match on a real question
against the shipped report scores well above 3 (e.g. 15.1 for the Amazon
RSU query below); a fully unrelated query scores exactly 0 (no token
overlap at all, since IDF only contributes for tokens present in some
chunk). A tiny single-incidental-term overlap sits closer to 0.3–0.9 on a
short chunk. `1.0` sits above that noise floor and well below any real hit,
so an unrelated query returns nothing rather than a weak, misleading match,
while genuine matches always clear it. (Note: the scorer also hard-filters
`score > 0` regardless of `min_score`, so a literal zero-overlap chunk is
never returned even if an operator sets `KNOWLEDGE_MIN_SCORE=0`.)

## TDD trail

Wrote `backend/tests/test_knowledge.py` against the not-yet-existing
`app.knowledge` package first; first run:

```
ModuleNotFoundError: No module named 'app.knowledge'
```

(all 19 tests errored at collection). Implemented `chunking.py` + `bm25.py`
+ `index.py`; re-ran just the module-level tests (chunking/retrieval/real
report) — one failure (`test_retrieve_mtime_invalidation_without_restart`):
a single-chunk corpus's BM25 scores couldn't clear the default 1.0 floor
even for a genuine 2-term match (IDF over N=1 chunk is intrinsically small).
Fixed by having that test pass `min_score=0.0` explicitly (it's testing
invalidation, not score tuning) — which then exposed a real bug: with
`min_score=0.0`, a *zero-score* (no-overlap) chunk was passing the `s >=
min_score` filter. Fixed by requiring `s > 0` unconditionally in
`index.retrieve()`, independent of the configured floor. After that: 10/10
chunking+retrieval+integration tests green. Then wired the chat endpoint
and the search endpoint against the remaining 6 (chat augmentation ×3,
endpoint ×3) — green on first run once the plumbing was in place.

## Gates

```
cd backend && ../.venv/bin/python -m pytest tests/ -q
# 136 passed (was 120; +16 new, 0 regressions)

cd dashboard && npx tsc -b
# clean, no output (untouched — no TS changes needed)
```

## Sample retrieval output (real shipped report, `docs/knowledge/pacific_northwest_luxury_real_estate_report_2026.md`)

```
QUERY: How does Amazon RSU vesting affect a buyer down payment strategy?
  15.137  ... > 1. The Tech Wealth Engine & Offer Structuring > Equity Compensation Mechanics: The Vesting Variance
   5.677  ... > 4. The School District Premium & Relocation Calculus > Advanced Buyer Profiling: The 2026 Tech Demographic Matrix
   5.478  ... > 1. The Tech Wealth Engine & Offer Structuring > 10b5-1 Trading Plans and Escrow Timing Constraints

QUERY: What is the Washington capital gains tax on a home sale?
   8.883  ... > 2. 2026 Taxation & Policy Implications > The Washington Capital Gains Tax (The 9.9% Surtax)
   8.669  ... > 1. The Tech Wealth Engine & Offer Structuring > Securities-Backed Lines of Credit (SBLOCs) and Margin Loans
   8.665  ... > 2. 2026 Taxation & Policy Implications > The Washington Capital Gains Tax (The 9.9% Surtax)

QUERY: How does the Bellevue school district affect home prices?
   7.162  ... > 3. Micro-Market Dynamics ... > Bellevue: The Core of Eastside Luxury > West Bellevue & Enatai ($2.5M to $2.7M+)
   6.787  ... > 4. The School District Premium & Relocation Calculus > Quantifying the Public School Premium (BSD vs. LWSD)
   6.195  ... > 3. Micro-Market Dynamics ... > Bellevue: The Core of Eastside Luxury > The $1.5M to $1.8M Floor

QUERY: What is the best pizza topping?
  (no hits)
```

Top hit heading is the intuitively correct section for all three domain
queries; the unrelated query returns nothing, as designed.

## Deviations from the brief

- None material. The brief anticipated grepping for stale references to the
  old report path elsewhere in the repo; there were none (only the file
  itself), so no extra edits were needed there.
- Endpoint lives in a new `backend/app/routers/knowledge.py` rather than
  being folded into `misc.py` — kept it separate since it's a distinct
  concern (retrieval, not CRM CRUD/metrics) and the brief's own framing
  ("new package... keep files small and focused") pointed the same
  direction for the router too.

## Concerns / follow-ups worth a look

- `KNOWLEDGE_MIN_SCORE=1.0` was tuned against one corpus (the shipped
  report, ~40 chunks). If an operator swaps in a very small doc (few
  chunks, low N), BM25 IDF shrinks and genuine matches can score lower —
  same shape of issue the mtime-invalidation test hit. Not a bug, just a
  tuning knob operators may need to lower (`KNOWLEDGE_MIN_SCORE=0` is safe
  floor-wise since the `score > 0` guard still filters true non-matches).
- The augmented chat message is a plain string prepend/delimiter — I did
  not attempt to token-budget it against the driver's context window; a
  very large `KNOWLEDGE_TOP_K` against a much bigger future corpus could
  grow the prompt more than intended. Default `k=3` keeps this modest today.
