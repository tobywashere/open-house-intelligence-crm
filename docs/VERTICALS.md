# Adapting this CRM to your industry

OpenHouse Intelligence was built for real estate, but the engine underneath is
industry-agnostic: lead intake from messy text, duplicate merging, scoring,
drafted follow-ups, booking, neglect detection, deterministic analytics, a
morning briefing, and local-AI chat grounded in your own documents. None of that
cares what you sell.

What *is* industry-specific lives in a **vertical pack**: one JSON file plus a
folder of knowledge documents. Swap the pack, get the same product for another
industry — no code changes.

```
verticals/
  real-estate/          ← the default
    pack.json
  b2b-saas/             ← example
    pack.json
    knowledge/b2b-saas-deal-desk-reference.md
  insurance/            ← example
  recruiting/           ← example
```

Select one with the `VERTICAL` environment variable:

```bash
VERTICAL=recruiting bash scripts/serve.sh
```

Everything falls back to real-estate defaults, key by key, so a partial or
malformed pack degrades gracefully instead of breaking the UI.

## What a pack controls

| Key | What it changes |
|---|---|
| `display_name` | Human name for the vertical |
| `brand` | The two-tone app wordmark (`name` + `name_accent`) |
| `stages` | Funnel stage keys, labels, and the rule deciding which leads land in each |
| `labels` | Display names for the `budget` / `area` / `timeline` / `intent` fields |
| `intent_values` | Allowed `intent` values and their labels |
| `personas` | Persona names, and which one is the default |
| `persona_rules` | How a lead's persona is inferred (first match wins) |
| `persona_recommendations` | The advice line shown per persona in the briefing |
| `schedule_titles` | Titles for briefing schedule blocks, keyed by intent |
| `copy` | ~26 user-visible strings across booking, chat, inbox, lead, funnel, notes, export |
| `mock_summary` | Sample daily-summary content shown in mock mode |
| `research` | Scope for the daily market-search prompt (see below) |

### What a pack does *not* control

`leads.status` is a **schema** enum — `new`, `contacted`, `meeting_booked`,
`closed` — shared by the database, the API, and the agent tools. Packs rename
what the funnel *calls* those stages and change which leads count toward each,
but not the underlying values. Same for the column names `budget`, `area`,
`timeline`, `intent`: only their labels are pack-driven. This is deliberate —
the frozen contract in [`CONTRACT.md`](CONTRACT.md) is what lets the agent, the
backend, and the dashboard evolve independently.

## Stage rules

Each stage carries a `rule` deciding which leads count toward it:

| Rule type | Meaning |
|---|---|
| `all` | Every lead |
| `status_is` | Status exactly equals `status` |
| `status_at_least` | Status rank ≥ `status` |
| `status_at_least_or_score` | Rank ≥ `status`, **or** rank ≥ `score_status` and `score` ≥ `min_score` |
| `event_type_or_status` | Lead has an event of `event_type`, **or** status equals `status` |

The real-estate `qualified` stage shows the compound form:

```json
{ "key": "qualified", "label": "Qualified",
  "rule": { "type": "status_at_least_or_score",
            "status": "meeting_booked", "score_status": "contacted",
            "min_score": 70 } }
```

Both thresholds are named explicitly rather than implied by rank arithmetic —
"already booked a meeting, **or** contacted and scoring 70+".

A pack needs **at least two valid stages**; with fewer, the funnel's conversion
math has nothing to compare and the loader falls back to the real-estate
defaults (labels included, which will look wrong — so check the logs if your
funnel says "Tours booked"). Rules with an unknown type are dropped.

## Persona rules

`persona_rules` is an ordered list; the **first** match wins, and the last entry
must be unconditional (`"when": null`) so every lead resolves to something.

```json
{ "persona": "Enterprise Buyer",
  "when": { "field": "budget", "op": "gte", "value": 100000 } }
```

Fields: `intent`, `budget`, `timeline`, `name`, `preferences_text`.
Ops: `eq`, `lt`, `lte`, `gt`, `gte`, `regex` (with optional `flags`).
Combine with `any` (OR) or `all` (AND), nested if needed:

```json
{ "persona": "Technical Evaluator",
  "when": { "any": [
    { "field": "preferences_text", "op": "regex", "value": "security|api" },
    { "field": "name", "op": "regex", "value": "&| and ", "flags": "i" } ] } }
```

Two things to know: regexes are validated with Python's engine at load time but
executed by the browser's, and the grammars differ slightly — a pattern using
Python-only syntax such as `(?P<name>…)` will pass validation and then be
ignored client-side. And `{"all": []}` matches *everything* (vacuous truth), so
an empty `all` short-circuits every rule after it.

Malformed rules are dropped at load time with a warning rather than shipped to
the browser. If a whole rule list is invalid, the real-estate rules are used.

## Merge semantics

Most keys **merge** with the defaults, so a pack overriding one label keeps the
rest. Four keys **replace wholesale**: `research`, `mock_summary`,
`schedule_titles`, and `persona_recommendations`. Those carry vertical-specific
*content* — a recruiting pack supplying only a `research.role` must not inherit
Seattle's regions and start searching for ADU legislation on a recruiter's
behalf.

One consequence worth knowing: supplying an **empty** object for a
replace-wholesale key keeps the default, because the loader treats empty as
"not provided". "Deliberately no schedule titles" isn't currently expressible.

## Knowledge documents

Anything you drop in the active pack's `knowledge/` folder is indexed and
becomes context the agent can consult — a local BM25 search, no embeddings, no
model download, no network call. Two paths reach it:

- **`search_knowledge`**, an agent tool. The model decides when it needs domain
  context. This is the precise path.
- **Automatic injection** into chat turns, best-effort. It can occasionally pull
  an unrelated section on generic scheduling chatter ("what's on my calendar").
  Harmless but imperfect; the tool path avoids it.

### Writing a document that retrieves well

- **Structure it with headings.** The index chunks on `##`/`###` boundaries, and
  the heading path is searchable, so a section titled "Open Reserves" is findable
  by that phrase even if the prose says it differently.
- **Put the specific term in the heading** — jargon, acronyms, proper nouns.
  Rare terms are what make retrieval confident.
- **Keep sections focused.** Very long sections are split on paragraph
  boundaries, which dilutes their scoring.
- **Write in the vocabulary your team actually uses**, since queries come from
  the same people.

Retrieval intentionally returns nothing when a query is just CRM chatter, so
"remind me to call Dana" won't inject market context. If a genuine domain query
returns nothing, lower `KNOWLEDGE_MIN_SCORE`.

The example packs ship a knowledge document each. They are AI-written
illustrations of structure — clearly labelled in-file — not researched industry
guidance. Replace them.

### Uploading from the dashboard

Not yet — knowledge documents are added by dropping `.md` files into the folder.
An upload panel is planned.

## The daily research scope

The market-news search is the one **internet-dependent** part of the product
(the CRM briefing itself works fully offline). Its scope lives in `research`:

```json
"research": {
  "role": "a market analyst for a commercial insurance brokerage",
  "audience": "a commercial insurance broker",
  "lookback_days": 7,
  "regions": ["Washington State", "Oregon"],
  "topics": ["Carrier appetite and underwriting-guideline changes"],
  "exclusions": ["personal lines consumer tips"],
  "national_scope_note": "Do NOT summarize national news unless…"
}
```

These render into [`prompts/market-news-reporter.md.template`](../prompts/market-news-reporter.md.template).
Because keywords need tuning once real results come back, they're also editable
at runtime through `GET`/`PUT /api/research-settings` — a saved setting wins over
the pack's defaults, and the rendered prompt is returned alongside the fields so
you can see exactly what the agent will be asked.

## Building your own pack

1. Copy the closest example: `cp -r verticals/b2b-saas verticals/my-industry`
2. Set `name` to the directory name — a mismatch means the loader silently
   serves defaults.
3. Work through the table above. Every `copy` key must be present; a missing one
   falls back to real-estate wording, which is how "Book a tour" ends up in a
   recruiting install.
4. Replace the knowledge document with your own.
5. Run it: `VERTICAL=my-industry bash scripts/dev.sh`
6. Check the funnel, the leads inbox, a lead profile, and the daily-summary
   overlay for wording that still sounds like real estate.

The shipped packs are validated by `backend/tests/test_shipped_packs.py`, which
asserts every pack loads as itself, carries the full key surface, is genuinely
re-skinned, and ships a retrievable knowledge document. Put your pack in
`verticals/` and those tests cover it automatically.

## Related documents

- [`CONTRACT.md`](CONTRACT.md) — the frozen schema, API, and agent-tool contract
- [`LOCAL-AI.md`](LOCAL-AI.md) — running a real local model
- [`../README.md`](../README.md) — quickstart and project layout
