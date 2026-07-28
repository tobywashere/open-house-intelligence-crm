# Vertical packs — design

**Date:** 2026-07-28 · **Owner:** Johaan · **Status:** approved

Make OpenHouse Intelligence adaptable to any sales vertical without forking the
code. Today the engine is already industry-agnostic — lead intake from messy text,
dedupe/merge, scoring, drafted follow-ups, booking, neglect detection,
deterministic analytics, morning briefing, local-AI chat with BM25 retrieval over
the operator's own documents. What is real-estate-specific is a thin, enumerable
surface: funnel stage names and rules, a few field display labels, persona names,
~41 UI copy strings, and the knowledge corpus.

This work turns that surface into a **vertical pack**: one config file plus a
knowledge directory. Swap the pack, get the same product for another industry.

Origin: Chris raised it in the team chat on 2026-07-28 — "whether anyone could use
what was built so far to make into an OpenClaw sales CRM for any industry." Four
commitments were made in that thread and all four are in scope here: make it
genuinely adjustable, ship example knowledge docs for other industries, document
how to adapt it, and let users upload their own knowledge doc from the UI.

Decisions locked with Johaan (2026-07-28):
- **Config pack, not schema rename.** `docs/CONTRACT.md` stays frozen; no column
  renames. Every skill, seed script, and the ~154-test suite keep working.
  Genericization happens at the display/label layer and in funnel configuration.
- **Three short example packs**, ~800–1,200 words of knowledge each, clearly
  labeled as illustrative samples — enough to prove retrieval works in another
  vertical without fabricating 4,000 words of fake domain expertise.
- **Full knowledge management panel** in the dashboard: upload, list, delete,
  and a search box to test retrieval.
- Spec → plan → subagent execution with reviews, same as the offline-first release.

## What a vertical pack is

A directory under `verticals/<name>/` containing:

```
verticals/real-estate/
  pack.json          # stages, labels, personas, copy strings
  knowledge/*.md     # the retrieval corpus for this vertical
```

`pack.json` covers exactly the surface that is industry-bound:

- **Funnel stages** — ordered list of `{key, label, rule}`. Two rules today are
  derived rather than status-backed (`qualified` = reached contacted AND
  `score >= threshold`; `offers` = has an event of a configured type). The rule
  vocabulary must express those two without new schema.
- **Field display labels** — `budget`, `area`, `timeline`, `intent` keep their
  column names; the pack supplies what the UI calls them (e.g. "Deal size",
  "Territory", "Urgency") and the allowed `intent` values with their labels.
- **Persona names** — the briefing/inbox persona chips (`Home Buyer`,
  `Luxury Executive`, …) become pack-supplied, with a default color mapping.
- **Copy strings** — the ~41 real-estate phrases in the dashboard ("Book a tour",
  "Tour booked", chat placeholder examples, empty states) become keyed lookups
  with the real-estate values as defaults.

Resolution order: pack value → built-in default. A missing or partial pack must
degrade to today's real-estate behavior, never to a crash or blank UI.

## Shipped packs

- `real-estate` — the default; today's behavior, extracted verbatim so the
  refactor is provably a no-op.
- `b2b-saas` — pipeline stages through demo/POC/procurement; knowledge sample on
  deal-desk and procurement mechanics.
- `insurance` — brokerage renewal cycle; knowledge sample on carrier appetite and
  renewal timing.
- `recruiting` — candidate pipeline through screen/onsite/offer; knowledge sample
  on compensation banding and offer negotiation.

Each pack's knowledge doc carries a visible header marking it as an illustrative
sample written to demonstrate structure, not verified industry guidance.

## Knowledge management UI

A dashboard section backed by additive endpoints:

- `POST /api/knowledge/docs` — upload a `.md` (base64 JSON, mirroring the
  existing `POST /scan-card` pattern). Markdown only, size-capped, filename
  sanitized to a safe slug, written into the active knowledge directory,
  index invalidated on next retrieval via the existing mtime check.
- `GET /api/knowledge/docs` — list indexed docs with chunk counts.
- `DELETE /api/knowledge/docs/{name}` — remove one.

Writes are audited (the contract's guarantee is that every REST write audits —
these must not break it). The existing `GET /api/knowledge/search` powers the
"test your doc" box so a user can confirm their upload is actually retrievable.

Security posture carried forward from the offline-first release: these are writes
to the server's filesystem, so path traversal must be impossible, content type
must be verified rather than trusted, and the endpoints sit behind the existing
`OHI_API_TOKEN` guard like every other `/api/*` route.

## Documentation

`docs/VERTICALS.md` — how to adapt the product to a new industry: what a pack
contains, how to write `pack.json`, how to write a knowledge doc that retrieves
well (heading structure matters — the index chunks on headings), how to upload it,
and what remains genuinely real-estate-shaped if you go deeper. Written for an
outsider who cloned the repo, consistent with the Task 13 docs standard.

## Non-goals

Runtime vertical switching (a pack is chosen at configuration time, not per
request), multi-tenant packs, schema column renames, migrating existing installs
between verticals, a pack marketplace, and PDF/DOCX ingestion (markdown only).

## Testing strategy

The real-estate pack must be a proven no-op: the existing suite passes unchanged
with it active. Pack loading gets its own tests (missing file, partial file,
malformed JSON, unknown stage rule — all degrade to defaults). Upload endpoints
get the same adversarial treatment the scan endpoint received: traversal
attempts, wrong content type, oversize, duplicate names. The 12 locked retrieval
acceptance queries stay untouched.

## Risks

- **Copy extraction is wide, shallow work** across ~41 strings; the risk is a
  missed string leaving "tour" in a recruiting install. Mitigation: grep-driven
  inventory checked into the plan, and a test asserting no known real-estate term
  appears in rendered output under a non-real-estate pack.
- **Upload is a new write surface** on a product whose thesis is local safety.
  It inherits auth and gets explicit traversal/type tests.
- Example knowledge docs are AI-written samples; they must be labeled as such
  in-file so nobody mistakes them for researched guidance.
