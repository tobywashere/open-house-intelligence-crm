# OpenHouse Intelligence — team update

**From:** Johaan · **Date:** 2026-07-28 · **For:** K, Chris, Toby

Since the hackathon I've taken the whole codebase through a full audit, a three-part
code review, and a 14-task hardening plan. The short version: **the project is now
safe for someone other than us to run, and it's ready to publish as open source.**
33 commits, 120 backend tests (up from 26), all green.

This touches all three workstreams, so here's what changed and why.

---

## Why I did this

Two things prompted it. First, a docs pass turned up that the README still described
an hour-one hackathon setup — pointing K at an `agent/` directory that never existed,
missing the GB10 deployment entirely. Second, and more seriously, a whole-project
code review found real defects that our demo happened not to trigger.

The most important ones:

- **Double-booking was reproducible.** The 409 conflict check wasn't atomic —
  8 concurrent identical booking requests all returned 200 and created 8 overlapping
  appointments. On stage, the agent and the dashboard booking the same slot would
  have done it.
- **Live integrations could freeze the whole server.** A slow Composio call inside an
  async endpoint stalled the event loop; a hung CLI call would block *every* request
  for up to 60s.
- **The agent's `delete_lead` tool had never worked** — it called an undefined
  function and raised `NameError`. Nothing imported the skills, so no test caught it.
- **Inbound email reached the agent that holds a live Gmail send tool**, with the only
  guardrail being a sentence in a SKILL.md — and the email poller defaulted **on**.

None of this was sloppiness; it's what a 36-hour build looks like when it survives
contact with real use. All of it is now fixed and tested.

---

## What changed, by area

### Backend (was Toby's)

- **Every request is now one `BEGIN IMMEDIATE` transaction** (`db.py`), which killed
  double-booking and the whole read-modify-write race class. There's a real
  concurrency test — 8 threads against a live server, exactly one 200.
- Integration hooks moved off the event loop; a slow Composio call no longer blocks
  other requests.
- **One timezone convention everywhere**: naive local wall-clock at every boundary.
  This fixed a live bug where dashboard-created reminders landed in Google Calendar
  7–8 hours off. Existing databases self-heal — there's a migration that converts
  legacy `Z`-suffixed rows on startup, and it's been hostile-tested (19 adversarial
  values, including ones that would otherwise have bricked startup).
- Status transitions are validated (you can no longer un-close a closed lead, including
  via the booking side door), inputs are bounded, `.ics` export escapes properly
  (a lead name could previously inject a second calendar event), and scan-card
  sniffs magic bytes instead of trusting the filename.
- `avg_response_minutes` is computed from real event deltas — it used to be
  hardcoded to `4`, which is a bad thing to show a judge.

### Agent & skills (was K's)

- Fixed the dead `delete_lead`, raised the CRM timeout to 120s (10s guaranteed false
  failures on the 3-call process endpoint), and added a smoke test that imports every
  skill tool — that's what would have caught it originally.
- **Composio guardrails are now enforced in code, not prose**: an allowlist of
  approved tool slugs, and recipient validation that's deny-by-default. That last one
  took three review rounds because the first two versions were bypassable — the
  reviewer pulled the actual Gmail tool schema and found `to` is an alias for
  `recipient_email` and `extra_recipients` exists, and an empty recipient set was
  *failing open*. It now refuses unknown argument keys entirely, so a future schema
  change fails closed.
- The email poller is **opt-in** now, and untrusted email text is wrapped in a
  nonce-tagged delimiter the sender can't escape.
- Skills work from any home directory — they had `/home/dell` hardcoded, so anyone
  else copying them per our own instructions got an `ImportError`.
- New `list_appointments` tool, because the briefing skill was told to fetch "leads
  with an appointment today" with no way to know who those were — a same-day
  appointment for a non-priority lead would have silently vanished from the schedule.

### Dashboard & product (mine)

- Fixed the insights write-through (a dashboard left open overnight never wrote the
  new day's row — the wall-mounted use case), a chat panel deadlock, duplicate
  fetching on every 60s tick, and error toasts where failures were silent.
- The daily briefing **now runs offline from live CRM data** instead of showing mock
  content. Mock mode is explicitly labeled "sample data"; when live with no briefing
  yet, it says so instead of faking one. Market-news is clearly the internet-optional
  part.

### Security posture

Worth calling out because the final review caught something none of the per-task
reviews could see. We had `allow_origins=["*"]` with a comment saying "tighten if
this ever leaves the demo." With the shipped defaults, **any website the operator
visited could read and write the CRM** through the browser — localhost binding
doesn't stop that. Directly contradicted our "PII never leaves the machine" pitch.
Now restricted, with a test that asserts a foreign origin gets no CORS header.

Also: the server binds `127.0.0.1` by default (LAN/Tailscale exposure is opt-in via
`HOST`), and there's an optional `OHI_API_TOKEN`.

---

## Open-source readiness

- **MIT licensed**, CONTRIBUTING.md, GitHub Actions CI (pytest + typecheck + build).
- **README rewritten around the pitch** — the vision from Chris's deck leads, with an
  Origins section crediting the four of us and the Dell × NVIDIA BuilderBase
  finish. The deck itself is in `docs/OpenHouse-Pitch.pdf`.
- **`docs/LOCAL-AI.md`** is a hardware-agnostic setup guide — the GB10 is now "one
  example deployment" rather than the architecture. Any tool-capable local model works.
- `.env.example` covers every variable; `docs/CONTRACT.md` is re-frozen and now
  *exactly* true (the old "every tool call is audited" claim was false for reads and
  for all real email — three write endpoints weren't audited at all; they are now).
- Personal info scrubbed. Notably, AI-drafted client emails were hardcoded to sign
  **"Best, Johaan"** — every future user's emails would have gone out with my name.
  Now `AGENT_DISPLAY_NAME`, unsigned when unset.
- `PLAN.md` and `TODO.md` moved to `docs/history/` — they're our internal scratch and
  read oddly in a public repo.

---

## What this means for each of you

**Nothing is blocked on you, and nothing you built was thrown away.** I folded all
three workstreams into one maintainer role to get this done — the old "contract
changes need all three of us in a room" rule is retired in favor of issue/PR
discussion, which is what an outside contributor can actually participate in.

- **K** — the agent skills are meaningfully more robust: real guardrails, working
  tools, and a briefing path that runs offline from the CRM. Your OpenClaw + Qwen
  setup is documented as the reference deployment. The cron config in LOCAL-AI.md is
  marked *illustrative* because I couldn't verify OpenClaw's exact schema from the
  repo — if you can confirm it, that's a nice one-line contribution.
- **Toby** — the backend changes are all additive or bug fixes; the schema shape is
  unchanged and the contract is re-frozen, not rewritten. Existing databases migrate
  themselves on startup.
- **Chris** — the pitch is now the README's opening, and the deck is committed. The
  product's offline-first claim is finally *true* end to end rather than aspirational.

## Where it stands

Everything is committed to local `main` and **not yet pushed** — I wanted you to see
this first. Say the word and it goes up.

If we publish: the final reviewer's verdict was "publish as-is," with ~25 deferred
minor findings logged (none blocking, all documented). Good first issues for anyone
who wants them.
