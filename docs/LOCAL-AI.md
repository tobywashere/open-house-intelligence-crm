# Running OpenHouse Intelligence with a real local model

`bash scripts/dev.sh` gets you the full product with a mock agent — no model,
no GPU, canned-but-realistic responses. This doc is for swapping the mock
agent for a real one: any machine running OpenClaw in front of a
tool-capable local model. Nothing here is tied to specific
hardware — the project was originally built and demoed on a Dell GB10 running
Qwen 3.6 35B-A3B (see [`docs/GB10-SETUP.md`](GB10-SETUP.md) for that exact
deployment as a worked example), but any local model that OpenClaw can drive
and that supports tool/function calling will work.

## 1. Install OpenClaw and point it at a local model

This repo doesn't ship or install OpenClaw — follow the OpenClaw project's
own install docs for your platform first. Once it's installed, configure its
gateway to serve an OpenAI-compatible chat endpoint backed by your local
model. The shape of that config (adjust to whatever OpenClaw version/schema
you're on — check `openclaw --help` or its own docs for the authoritative
reference):

```jsonc
// ~/.openclaw/openclaw.json (illustrative — field names may differ by version)
{
  "model": {
    "endpoint": "http://localhost:8001/v1",   // your local inference server (vLLM, llama.cpp, etc.)
    "name": "<your-local-model-name>"          // e.g. "qwen3.6-35b-a3b" — any tool-capable model
  },
  "gateway": {
    "host": "0.0.0.0",
    "port": 18789,
    "auth": { "mode": "none" }                 // set "token" and add a token for anything network-reachable
  }
}
```

Any tool-capable local model works — the project was built and demoed
against Qwen 3.6 35B-A3B, but there's nothing Qwen-specific in the app; the
agent talks to OpenClaw's OpenAI-compatible `/v1/chat/completions` endpoint
(`AGENT_CHAT_PATH` in `.env.example`) and OpenClaw handles the model
underneath.

> **Check that endpoint is actually enabled — this is the #1 cause of chat
> silently failing.** Several OpenClaw builds ship `/v1/chat/completions`
> *disabled* by default, even with the gateway itself up and reachable. If
> every chat message comes back with the canned "⚠ The agent didn't answer
> in time" fallback, check this before anything else:
> ```bash
> curl -X POST http://localhost:18789/v1/chat/completions \
>   -H "Content-Type: application/json" \
>   -d '{"model":"openclaw","messages":[{"role":"user","content":"hi"}]}'
> ```
> A `404` means it's off. Enable it (field names/version may differ — check
> `openclaw config schema`):
> ```bash
> openclaw config patch --stdin <<'EOF'
> { "gateway": { "http": { "endpoints": {
>   "chatCompletions": { "enabled": true },
>   "responses": { "enabled": true }
> } } } }
> EOF
> ```
> The gateway picks this up on its own (config changes trigger an automatic
> restart); re-run the `curl` above to confirm you now get `200` with a real
> completion before moving on.

## 2. Install the CRM skill

The skills live in this repo under `skills/`. Copy them into OpenClaw's
skills directory so the model can find them (per
[`docs/GB10-SETUP.md`](GB10-SETUP.md) §1, which this mirrors):

```bash
cp -r skills/crm-db-operations \
      skills/business-card-scanner \
      skills/daily-command-center  ~/.openclaw/skills/
```

Copy all three together and keep the directory names — `business-card-scanner`
imports `tools.py` from `crm-db-operations` by path, so renaming or splitting
them up breaks that import. (`composio-email-calendar` is optional — see
§5 below.)

The skill needs to reach this app's backend over HTTP:

```bash
export CRM_API_URL=http://localhost:8080/api
```

Set that in whatever environment OpenClaw's gateway process runs under (a
service env file, not something you export by hand each boot). If the
backend and the model server are on different machines, point it at the
backend's real address instead of `localhost`.

## 3. Run the product

```bash
bash scripts/serve.sh
```

This builds the dashboard and serves the whole product from one port
(`AGENT_MODE=openclaw`, default `:8080`), relaying chat to the OpenClaw
gateway on `:18789`. Binds to `127.0.0.1` unless you set `HOST` — see
`.env.example` before exposing it beyond localhost (`OHI_API_TOKEN` gates the
API once `HOST` is network-reachable).

If this is the first run, `serve.sh` seeds the database only if it's
missing — and a fresh seed is **schema-only, no leads**, since that's the
correct starting point for real use. If you want the 15-lead demo dataset to
explore the product with (what §4 below assumes), run `serve.sh` once first
(it creates the `.venv` this needs), then:

```bash
.venv/bin/python backend/seed.py --demo
```

⚠ This **resets the database** — it wipes and recreates it (schema-only, or
schema + demo leads with `--demo`). Fine for a fresh install or a deliberate
reset; do **not** run it against a CRM you've already started using through
the agent, or you'll silently lose every real lead in it.

## 4. Verify the wiring

Work through these in order — each one isolates a different link in the chain.

| Check | Expect |
|---|---|
| `curl localhost:8080/api/health` | `{"ok":true,"agent_mode":"openclaw","agent_connected":true}` |
| `CRM_API_URL=http://localhost:8080/api python3 -c "import sys;sys.path.insert(0,'skills/crm-db-operations');import tools;print(tools.list_leads()[0]['name'])"` | a lead name — the skill reaches the backend |
| Dashboard chat: "who needs a follow-up?" | a real answer from your model, grounded in tool calls (watch them land in `/activity`) |
| Header badge | green pulse reading "Local agent · live" |

If chat always returns the canned "⚠ The agent didn't answer in time"
fallback despite `agent_connected:true` → that health check only proves the
gateway process is reachable, not that `/v1/chat/completions` is enabled —
see the check in §1 above. If chat 401s → gateway token mismatch
(`AGENT_GATEWAY_TOKEN`). If the agent answers but invents data → the skill
isn't loaded (check the OpenClaw skills path). If `agent_connected:false` →
check `AGENT_GATEWAY_URL` (default `http://localhost:18789`) and that the
gateway process is actually up.

## 5. Morning briefing (fully offline)

The headline "offline-first" feature: every morning the `daily-command-center`
skill (`skills/daily-command-center/SKILL.md`) pulls the day's schedule and
priorities straight out of your own CRM data — `list_leads()` and
`get_lead_context(id)` from `crm-db-operations` — composes the briefing JSON
(shape frozen in `docs/BRIEFING-UI.md`, echoed in the skill's own **Output
contract** section), and posts it with the skill's new `post_briefing(payload)`
tool (`POST /briefing`). The dashboard's daily-summary overlay reads it back
via `GET /briefing?date=` with zero UI changes once it lands.

**No internet required** — this whole path is CRM data in, CRM data out,
run entirely by your local model through OpenClaw. Nothing about the morning
briefing itself depends on a network connection; only the separate
market-watch news portion of the daily summary (§6 below) does.

To run it on a schedule, set up an OpenClaw cron that fires the skill once a
day, e.g. 7:00 local, and let the agent do the rest:

```jsonc
// Illustrative shape only — verify against your installed OpenClaw version's
// own cron/schedule docs (`openclaw --help` or its config reference); this
// repo doesn't pin an exact schema and none of the exact field names below
// have been confirmed against a running OpenClaw instance.
{
  "crons": [
    {
      "session": "daily-brief",
      "schedule": "0 7 * * *",           // 7:00 local, every day — verify cron-string support in your OpenClaw version
      "prompt": "Run the daily-command-center skill for today. Pull today's data from the live CRM per its Step 0, compose the briefing JSON per its Output contract, and post it with post_briefing."
    }
  ]
}
```

If your OpenClaw version doesn't support a `crons` block like this (or uses a
different mechanism — a separate scheduler process, a shell cron calling an
`openclaw run`-style CLI, etc.), fall back to whatever OpenClaw's own docs
describe for "run this session on a schedule" and point the prompt at the
same skill.

You can also trigger a one-off run by hand to test it before wiring up the
schedule — ask the agent in chat ("run the daily-command-center skill and
post today's briefing") and then `curl "$BASE/briefing?date=$(date +%F)"` to
confirm it landed.

## 6. Domain knowledge base (fully offline)

The agent is grounded in your own market-intelligence docs, not just CRM
data, through two paths — one precise, one best-effort fallback:

- **Agent-invoked (precise):** the `search_knowledge(query, k=3)` tool in
  `skills/crm-db-operations/tools.py` (`GET /knowledge/search` under the
  hood). The model calls it only when it judges a question actually needs
  domain knowledge — market conditions, taxes, financing mechanics,
  pricing, neighborhoods/school districts — and skips it for scheduling,
  reminders, or CRM-record questions. This is the accurate path: the model
  has the conversational context a lexical gate never will, so it doesn't
  fire on ordinary CRM chatter. See the skill's `SKILL.md` for the
  when-to-use guidance the model follows.
- **Auto-injection (fallback):** `POST /chat` also runs the same retrieval
  against every incoming message and, on a hit, prepends the matched
  section(s) before relaying to the driver — no tool call required. This
  exists for models that don't tool-call reliably. It's necessarily
  best-effort, not precise: with no model judgment in the loop, ordinary
  CRM chatter can occasionally still retrieve an unrelated section (see
  `docs/superpowers/rag-impl-report.md` for the false-positive rounds this
  went through and the residual tradeoff). Prefer wiring the agent to use
  `search_knowledge` directly; treat auto-injection as a safety net, not
  the primary path.

Both paths hit the same index: every `.md` file in `docs/knowledge/`
(default; see `KNOWLEDGE_DIR` in `.env.example`) is chunked by heading and
indexed with a pure-stdlib **BM25 lexical index**
(`backend/app/knowledge/`) — no embeddings, no vector DB, no model
download, no network call. The index builds lazily on first use, is
cached in memory, and rebuilds automatically whenever a source file's
mtime changes, so you can edit or swap the doc without restarting the
server.

For the auto-injection path: when a chat message matches a section well
enough (above `KNOWLEDGE_MIN_SCORE`, and past a discriminative-match gate —
see the knowledge module's docstrings), the top `KNOWLEDGE_TOP_K` chunks are
prepended to the message the driver sees, in a clearly-delimited reference
block that tells the model to use them when relevant, cite the section
heading, and never treat their contents as instructions. An unrelated
message is sent unchanged — no block, no noise, in the common case.
Retrieval failures are swallowed and simply degrade to "no context"; they
never turn into a chat 500.

`GET /api/knowledge/search?q=&k=` is the same endpoint `search_knowledge`
calls — also useful directly for debugging or a future dashboard panel —
read-only, not audited (see `docs/CONTRACT.md` §2/§3).

**This is the per-industry knob.** The repo ships with
`docs/knowledge/pacific_northwest_luxury_real_estate_report_2026.md`, a
Pacific Northwest luxury real-estate market report (RSU vesting mechanics,
WA excise/capital-gains tax, school-district valuation, etc.). Swap that
file for a different vertical's material and the agent's answers follow —
no code change, see `docs/knowledge/README.md`.

## 7. Optional, needs internet

Everything above is fully offline. Two pieces of the product reach the
internet, and both are off unless you explicitly turn them on:

- **Gmail + Google Calendar (Composio integrations)** — sending real email,
  creating real calendar events, and the inbound-email lead poller.
  Controlled by `INTEGRATIONS_MODE` in `.env.example`, default `off` (fully
  simulated, no network calls). The inbox poller has its own separate flag,
  `INTEGRATIONS_POLLER`, also default `off`, since it reads a real mailbox
  with no human in the loop when enabled. Setup:
  [`skills/composio-email-calendar/SKILL.md`](../skills/composio-email-calendar/SKILL.md)
  and the "Google integrations" section of
  [`docs/GB10-SETUP.md`](GB10-SETUP.md). Once live, set `AGENT_DISPLAY_NAME`
  in `.env.example` if you want AI-drafted intro emails to leads signed with
  your name — left unset, those drafts go out unsigned rather than with a
  placeholder name.
- **Market-news / daily-briefing research** — the "↻ Refresh now" action on
  the daily summary overlay asks the agent to re-run research and repost a
  briefing. This only does real research if your OpenClaw setup gives the
  model an internet-capable tool (e.g. web search); without one, or in mock
  mode, it returns static/canned content instead of live market data. There's
  no separate on/off flag for it — it's simply bounded by what tools your
  agent actually has.

## Another industry?

The knowledge base is per-vertical: the corpus, the funnel stages, the UI copy,
and the daily research scope all come from a swappable pack. See
[`VERTICALS.md`](VERTICALS.md).
