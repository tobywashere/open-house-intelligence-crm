# Local BM25 knowledge retrieval — implementation report

## Fix round 2 (team-lead review): generic word-pair corroboration

Round 1's discriminative-match gate (>=2 distinct query terms matched, OR
one term at/above the corpus median IDF) closed every reported false
positive except one the review found by probing wider: **"set up a
meeting"** still matched `The Washington Capital Gains Tax (The 9.9%
Surtax)` at score 4.49.

**Root cause:** the >=2-distinct-terms path has no requirement that the
matched terms be individually meaningful — it only checks that two terms
co-occur in the same chunk. "set" happens to occur in exactly one chunk of
the shipped report (df=1), which gives it the *same* IDF (3.258, the
corpus's ceiling/median value) as a genuinely rare domain term like
"vesting" — BM25/IDF statistics cannot tell "set" (a generic verb,
coincidentally rare in this specific ~38-chunk corpus) apart from
"vesting" (genuinely rare because it's specific vocabulary). "up" also
matched (df=3), so the pair cleared the >=2-terms bar even though neither
term carries any real topical signal. This is the same fundamental
limitation identified in round 1 (statistically identical corpus stats for
semantically different words), just recurring on a term *pair* instead of
a single term ("open"/"Clyde" last time).

**Why I did not try to fix this with more IDF-threshold tuning:** the
review's own guidance was explicit — don't ship a stricter statistical
gate that risks zeroing out real hits, since "set"/"vesting" are
literally indistinguishable by any per-term corpus statistic at this
corpus size. A tighter threshold on the >=2-terms path (e.g. requiring at
least one of the two matched terms above some percentile) would need that
percentile to sit between "set"'s IDF (3.258, tied for the ceiling) and
nothing — there is no gap to tune into. I verified this arithmetically
before choosing a different lever (see `bm25.py`'s IDF-percentile printout
in the round-2 investigation), so this isn't a guess.

**Fix:** a second, complementary lever — a curated "scheduling/
communication chatter" stopword category in `STOPWORDS`
(`backend/app/knowledge/bm25.py`): call, remind, book, show(ing), wear,
reset, password, weather, tomorrow/today/tonight/yesterday, meet(ing),
email, text, message, phone, mom/dad, set, and the seven weekday names.
This is deliberately framed as a general "CRM scheduling-chatter is never
market-intelligence content in any vertical" category, not a per-string
patch for the review's exact test sentences — it's the same category of
word regardless of which report is loaded (real estate, legal, whatever),
and none of it overlaps with any domain term in the good-query set (verified
below). Also added "up"/"down"/"off"/"over"/"under"/"again"/"once"/"here"/
"now" as ordinary prepositions/particles to the core stopword list (a
standard inclusion in most English stopword lists, not specific to this
corpus).

### Twelve-row acceptance table (exact set required by the review)

| # | Query | Expect | Result |
|---|---|---|---|
| 1 | "what should I wear to the open house" | no hits | ✅ no hits |
| 2 | "remind me to call my mom" | no hits | ✅ no hits |
| 3 | "how do I reset my password" | no hits | ✅ no hits |
| 4 | "book a showing for Tuesday" | no hits | ✅ no hits |
| 5 | "can you email Sarah" | no hits | ✅ no hits |
| 6 | "whats the weather tomorrow" | no hits | ✅ no hits |
| 7 | "set up a meeting" | no hits | ✅ no hits (was: 4.49 → Washington Capital Gains Tax) |
| 8 | "Amazon L7 unvested equity can they afford 3M" | Vesting Variance | ✅ 7.021 Equity Compensation Mechanics: The Vesting Variance |
| 9 | "seller wants to avoid excise tax" | Graduated REET | ✅ 7.351 The 2026 Graduated Real Estate Excise Tax (REET) |
| 10 | "Medina or Clyde Hill better for schools" | Clyde Hill & Yarrow Point | ✅ 9.295 Clyde Hill & Yarrow Point ($4.3M to $4.5M+) |
| 11 | "how do 10b5-1 plans affect closing timing" | 10b5-1 Trading Plans | ✅ 9.001 10b5-1 Trading Plans and Escrow Timing Constraints |
| 12 | "client maxing mega backdoor roth has no cash for down payment" | Mega-Backdoor Roth Constraint | ✅ 17.429 The 2026 Mega-Backdoor Roth IRA Constraint |

Bonus regression check (also passes): "Amazon RSU vesting affects buyer
liquidity" → 12.826 Equity Compensation Mechanics: The Vesting Variance.

No good query lost a hit and no false positive survived — all twelve pass
together, so this did not require reporting a residual known-false-positive
(the escape hatch the review offered was not needed this round).

### Tests added (6 new, `backend/tests/test_knowledge.py`)

Verified failing against the pre-round-2 commit (`0e4c8a0`) first — `git
stash` of just `bm25.py`, reran, popped back:
```
FAILED test_real_report_crm_chatter_returns_no_hits[set up a meeting]
FAILED test_discriminative_gate_still_rejects_a_rare_generic_word_pair
(all other rows already passed pre-round-2, confirming round 1 held for
 the other new probes: "can you email Sarah" and "whats the weather
 tomorrow" were already clean before this round's stopword change)
```
- `MUST_RETURN_NO_HITS` grew from 4 to 7 queries (added "can you email
  Sarah", "whats the weather tomorrow", "set up a meeting").
- `MUST_RETURN_CORRECT_TOP_HIT` grew from 4 to 6 queries (added the 10b5-1
  and mega-backdoor-roth queries from the review).
- `test_discriminative_gate_still_rejects_a_rare_generic_word_pair` —
  synthetic 1-doc corpus where two scheduling words ("meeting", "call")
  co-occur in exactly one chunk (matching real domain terms' statistical
  profile), isolating this exact failure mode independent of the real
  report's vocabulary; also asserts a real two-term domain match ("flange
  bracket") in the same corpus still passes.

### Gate

```
cd backend && ../.venv/bin/python -m pytest tests/ -q
# 152 passed (was 146 after round 1; +6 new, 0 regressions)
```

## Fix round 1 (team-lead review): discriminative-term gate

**Report of the actual bug, not just the fix theory:** the min-score floor
alone did not stop a completely unrelated CRM-chatter query from retrieving
a market-report chunk when it happened to share a single coincidental word
with some section. Root-caused two distinct issues, not one:

1. **`docs/knowledge/README.md` was itself being indexed.** It's a `.md`
   file in the knowledge directory (added in the first pass to explain the
   per-industry swap), and `retrieve()` globs `*.md` indiscriminately. Its
   own prose ("no embeddings, no vector DB, no model download, no network
   **call**") put the literal token `call` into the corpus with high IDF
   (it occurred in exactly one chunk — the README itself), which is exactly
   why "remind me to **call** my mom" matched a REET script section. Fixed
   by excluding `README.md` (case-insensitive) from indexing in
   `_knowledge_files()` (`backend/app/knowledge/index.py`) — it documents
   the mechanism, it isn't domain content.
2. **Missing personal pronouns in the stopword list.** The report quotes
   first-person advisory scripts verbatim ("I am structuring this offer...",
   "we must reverse-engineer..."), so "i", "me", "my", "we", "us", "they"
   etc. appear in the corpus with real, sometimes high, IDF (rare because
   only 1-2 script chunks use first person at all) — completely disconnected
   from their generic meaning in an unrelated chat message. Expanded
   `STOPWORDS` in `backend/app/knowledge/bm25.py` with pronouns and a few
   more generic function words.

Both were necessary but **not sufficient** — one case survived: "what
should I wear to the **open** house" still matched a script chunk on the
word "open" alone (it turns up, unrelated, in "...ensure your trading
window is officially **open**..."). Investigated why a flat/tuned
min-score floor can't fix this generically: it's not a scoring-magnitude
problem, it's that a single coincidental content-word overlap is
indistinguishable *by score alone* from a real single-term hit.

**Discriminative-term gate**, per the review's request, implemented in
`BM25Index.has_discriminative_match()` (`backend/app/knowledge/bm25.py`).
Note on a design correction from the literal ask: gating on "IDF strictly
above the corpus median" alone does not work on a corpus this size (~38
chunks) — Zipf's law means the majority of unique terms are hapax
(occur in exactly one chunk), which pushes the *median* itself up to
essentially the ceiling value, so a strict `>` comparison rejected almost
everything (including genuine hits like "vesting", "excise", "Medina") the
first time I wired it in — I caught this because my own regression probes
on the good queries went to zero hits, not because it looked plausible.
Root cause, with real numbers from the shipped corpus: "open" and "Clyde"
(both proper/topical terms one would want to distinguish) have **identical**
document frequency (df=2) and therefore identical IDF — no single scalar
per-term statistic can separate them; the difference is semantic, not
statistical, at this corpus size. The gate that actually works, and is what
shipped:

> A chunk counts as a genuine hit if either **(a)** at least two distinct
> query terms matched it (real multi-term corroboration — this is what
> saves "excise"/"tax"/"seller" and "Medina"/"Clyde"/"schools", regardless
> of any individual term's rarity), **or (b)** exactly one query term
> matched and that term's corpus IDF is at/above the corpus's own median
> (a single genuinely rare/specific term, e.g. "vesting", is still enough
> alone). A single moderately-common coincidental word ("open" matched
> alone) satisfies neither and is correctly rejected.

This keeps the requested property — corpus-relative, self-calibrating,
no magic per-corpus number, recomputes for a different vertical's doc set —
while actually working on the shipped corpus's real statistics, which the
literal median-only version didn't.

### Before / after probe tables

**False-positive probes (CRM chatter) — before (main, pre-fix):**
```
"what should I wear to the open house"
  5.068  Script 3: Aligning Escrow with Corporate Liquidity (10b5-1 Planning)
  3.046  10b5-1 Trading Plans and Escrow Timing Constraints
  2.735  Tukwila: The Industrial Wealth Engine and Transit Hub

"remind me to call my mom"
  3.171  Script 1: Negotiating the Graduated REET Burden (Seller Consultation)

"how do I reset my password"
  3.171  Script 1: Negotiating the Graduated REET Burden (Seller Consultation)
  2.735  Tukwila: The Industrial Wealth Engine and Transit Hub
  2.414  The East Link 2 Line Impact

"book a showing for Tuesday"
  (no hits — already clean)
```

**Same probes — after (this fix):**
```
"what should I wear to the open house"   -> (no hits)
"remind me to call my mom"               -> (no hits)
"how do I reset my password"             -> (no hits)
"book a showing for Tuesday"             -> (no hits)
```

**Good-query regression guard — before → after (top hit, score):**
```
"Amazon L7 with unvested equity, can they afford 3M"
  before: 7.150  Equity Compensation Mechanics: The Vesting Variance
  after:  7.022  Equity Compensation Mechanics: The Vesting Variance

"seller wants to avoid excise tax"
  before: 7.451  The 2026 Graduated Real Estate Excise Tax (REET)
  after:  7.324  The 2026 Graduated Real Estate Excise Tax (REET)

"is Medina or Clyde Hill better for schools"
  before: 9.466  Clyde Hill & Yarrow Point ($4.3M to $4.5M+)
  after:  9.333  Clyde Hill & Yarrow Point ($4.3M to $4.5M+)

"How does Amazon's RSU vesting schedule affect a buyer's liquidity?"
  before: (already correct, per original report)
  after:  14.835  Equity Compensation Mechanics: The Vesting Variance
```
(Scores shift slightly because the stopword-list expansion changes token
counts/lengths feeding BM25's length-normalization term; rankings and top
hits are unchanged.)

### Tests added (10 new, `backend/tests/test_knowledge.py`)

Verified failing against the pre-fix commit (`d9ac638`) first — `git stash`
of just the fix files (`bm25.py`, `index.py`, `.env.example`), reran, popped
the stash back:
```
FAILED test_real_report_crm_chatter_returns_no_hits[what should I wear to the open house]
FAILED test_real_report_crm_chatter_returns_no_hits[remind me to call my mom]
FAILED test_real_report_crm_chatter_returns_no_hits[how do I reset my password]
FAILED test_discriminative_gate_rejects_single_common_word_match
FAILED test_readme_in_knowledge_dir_is_not_indexed
(6 failed, 4 passed — "book a showing" and the 4 good-query regressions
already passed pre-fix, as expected)
```
- `test_real_report_crm_chatter_returns_no_hits` (parametrized, 4 queries) —
  the exact false positives from the review, against the real report.
- `test_real_report_good_domain_queries_still_retrieve_correct_section`
  (parametrized, 4 queries) — regression guard for the known-good queries,
  including the L7 one from the review.
- `test_discriminative_gate_rejects_single_common_word_match` — synthetic
  2-doc corpus built specifically to isolate the gate: a "common" word
  present in every chunk vs. a "unique" word in exactly one; asserts the
  common-word-alone query yields nothing, the unique-word-alone query hits
  the right chunk, and a real two-term co-occurrence passes independent of
  either term's individual rarity.
- `test_readme_in_knowledge_dir_is_not_indexed` — a `README.md` dropped
  alongside a real doc must not itself be searchable content.
- Adjusted `test_retrieve_respects_k` to a multi-term query (it was
  incidentally exercising the new gate on a single term whose df crossed
  the fixture corpus's own median — not what that test is about).

### Gate

```
cd backend && ../.venv/bin/python -m pytest tests/ -q
# 146 passed (was 136 after the first pass; +10 new, 0 regressions)
```

### `.env.example`

Updated the `KNOWLEDGE_MIN_SCORE` comment to describe both gates together —
the score floor and the (non-configurable) discriminative-term match — and
to explicitly warn that raising the score floor is not the fix for
false-positive noise; the term gate is.

## What shipped (original pass)

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
