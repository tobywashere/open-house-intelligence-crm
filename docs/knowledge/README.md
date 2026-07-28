# Knowledge base

Any `.md` file dropped in this directory is chunked (by heading), indexed
with a local BM25 lexical index (`backend/app/knowledge/`), and becomes
context the agent can retrieve when a client's chat message matches it —
see `docs/LOCAL-AI.md` for how this wires into `POST /chat` and
`docs/CONTRACT.md` §2 for the `GET /api/knowledge/search` debug endpoint.

No embeddings, no vector DB, no model download, no network call — this is
plain-stdlib lexical retrieval, in keeping with the product's offline-first
claim. The index builds lazily on first use, is cached in memory, and
rebuilds automatically when a file's mtime changes (edit a doc, no restart
needed).

**This is the per-industry knob.** `pacific_northwest_luxury_real_estate_report_2026.md`
is the Pacific Northwest luxury real-estate market intelligence report this
CRM shipped with. To retarget the whole agent at a different vertical or
market, swap the `.md` file(s) here for that domain's material — no code
change required. You can drop in multiple files; each is chunked and
indexed independently.

Config (see `.env.example`, Knowledge group): `KNOWLEDGE_DIR` (default
`docs/knowledge`, this directory), `KNOWLEDGE_TOP_K`, `KNOWLEDGE_MIN_SCORE`.
