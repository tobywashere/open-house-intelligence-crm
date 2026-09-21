# Native Create-Lead Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. User has authorized the read → propose → human approve milestone. Keep execution inside the isolated worktree; do not push/merge or change the original runtime.

**Goal:** One local native OpenClaw create-lead proposal flow with enforced identities and exactly-once human approval.

**Architecture:** Capability-authenticated FastAPI routes, one durable SQLite request-to-proposal link, one dedicated native proposal plugin, and the existing approval transaction/UI. Human credentials enter the running dashboard, not compiled assets.

**Tech Stack:** Python/FastAPI/SQLite, Node/OpenClaw plugin, React/TypeScript, pytest and existing Node/Playwright tests.

**Spec:** `docs/superpowers/specs/2026-09-21-native-create-lead-design.md`.

## Global Constraints

- Base `720cf521d81cf3b7ef184a796d3f83515bdf9de8`; branch `codex/native-create-lead`.
- Local inference; no automatic retries, cloud fallback, broad PR #7 import, additional write tools, production DB writes, or external integration calls.
- Capability mode requires distinct printable ASCII non-whitespace `OHI_API_TOKEN` and `OHI_AGENT_API_TOKEN`, each at least 32 characters. Agent may only `GET /api/leads` and `POST /api/agent/lead-proposals`, plus public health/auth status. Header/body claims cannot elevate identity.
- The new native proposal workflow is disabled outside capability mode. Tokenless/single-token legacy behavior is explicitly retained outside that workflow.
- Never bundle the human token into frontend assets, persist it in browser storage, or pass it to a gateway. Local server/fixture credential files must be private and excluded from version control and evidence. New proposal plugin requires only its agent CRM key and rejects a human CRM key in its process environment.
- Request IDs are lowercase 32-character hex strings; messages are 1–2000 characters. Native proposal gateway uses loopback HTTP, user `ohi-propose-<request_id>`, agent `native-proposals` by default, one completion request and a 60-second bound.
- Proposed fields: name 1–200 characters, optional email at most 320 and phone at most 100; no secondary extraction. Replays return existing durable state without another model request; conflicts return 409.
- Reuse existing approval transactions and UI. Model text never proves creation. Tests use isolated data; report local automated/simulated evidence separately from unperformed WSL live acceptance.

## Task 1: Enforced identity and runtime dashboard unlock

**Ownership:** `backend/app/auth.py` (new), `backend/app/main.py`, `backend/app/approvals.py`, auth tests, `dashboard/src/auth.ts` (new), `dashboard/src/components/AuthGate.tsx` (new), `dashboard/src/main.tsx`, `dashboard/src/api.ts`, `dashboard/src/nativeRead.ts`, minimal App lock control if needed, auth browser tests, existing read plugin credential selection, `.env.example`, relevant auth documentation. Do not implement native proposal storage/routes/plugin yet.

**Interfaces:**
- Middleware supplies trusted role/capability-mode request state. Export `require_human(request)` and `require_agent(request)` and a capability-mode/config validator for subsequent routes. Both role dependencies for the new native workflow require capability mode.
- Public `GET /api/auth/status` -> `{mode: "local"|"token"|"capabilities", role: "human"|"agent"|null}`.
- Dashboard `auth.ts` owns an in-memory token and common authenticated fetch/header helpers. AuthGate mounts the existing app only for a human/local session; protected 401 and Lock clear the token. Native reads/calendar downloads use the same helper.

- [ ] Write failing pytest cases using synthetic 32+ character human and agent tokens. Verify agent `X-Actor: user` cannot POST `/api/leads`, approve or deny, change settings, call general chat, or invoke human native orchestration. Verify missing/invalid tokens, invalid protected configuration, human access, public status and CORS, and retained legacy behavior.
- [ ] Implement the middleware/dependencies; change `is_agent_write` to use trusted identity in capability mode and preserve explicit legacy behavior outside it. The later agent proposal route may be allowlisted now; absent routes still return normal 404.
- [ ] Add runtime dashboard unlock and common token handling. Remove `VITE_API_TOKEN` references from application source and update its documentation. Add browser cases for invalid/agent rejection, human unlock, lock, and 401 reset (simulated status responses acceptable here; real API role behavior is covered by pytest).
- [ ] Let the read plugin prefer the restricted agent token when supplied, while retaining its legacy fallback. Do not change its permitted operation/schema.
- [ ] Run focused auth/backend/read/plugin tests, build with a synthetic `VITE_API_TOKEN` canary and verify it is absent from assets, then browser checks. Record dependencies reused and commit. Task review must approve spec and quality before Task 2.

## Task 2: Durable native create-lead protocol and plugin

**Ownership:** additive `backend/schema.sql`/`backend/app/db.py` request table, new `backend/app/native_proposals.py`, new `backend/app/routers/native_proposals.py`, route registration, `openclaw-plugins/openhouse-proposals/` plugin/tests, backend proposal/lifecycle tests, new proposal env docs. Also disable environment proxy use in the existing native-read HTTP client (minimal setting/regression only). Consume Task 1 auth; do not change its policy or implement the dashboard proposal panel.

**Interfaces:**
- `POST /api/chat/lead-proposal` (human) body `{request_id, message}`.
- `GET /api/chat/lead-proposal/{request_id}` (human) durable status.
- `POST /api/agent/lead-proposals` (agent) body `{request_id, name, email?, phone?}`.
- Responses: `{request_id, state: "running"|"unknown"|"failed"|"proposed"|"approved"|"denied", proposal: PendingChange|null}`; agent tool submission may wrap/return this same verified object. `PendingChange` is the existing parsed stored row.
- `NATIVE_PROPOSAL_GATEWAY_URL`, `NATIVE_PROPOSAL_GATEWAY_TOKEN`, `NATIVE_PROPOSAL_AGENT_ID` configure transport. Expose a clean injectable completion seam for tests; no production mock-provider mode.
- Local gateway clients must ignore ambient proxies (`trust_env=False`) as well as validate loopback URLs and reject redirects.

- [ ] Write failing tests at API/DB seams for reservation, role restrictions, same-ID replay/conflict, no lead before approval, duplicates with changed fields, and spoofed/nonexistent request IDs.
- [ ] Add `native_lead_requests` and durable reservation/status/queue helpers. Bind each ID to its original text, queue/link a proposal atomically, and reuse `pending_changes` dedupe where useful. Make `pending_id` a nullable unique foreign key; conditionally update an eligible unbound row and require one row changed. Avoid a second copy of approved/denied state.
- [ ] Implement one native completion and durable outcome resolution. Check existing status before gateway configuration/dispatch; validate configuration before fresh reservation. Persist `unknown` after uncertain errors and cancellation; convert unbound `running` rows to `unknown` on application startup. Use durable proposals even when completion fails; status checking and replay never redispatch. Accept a legitimate late proposal only for running/unknown requests. Enforce exact request ID and runtime session identity, including suffix/newline rejection.
- [ ] Implement the dedicated plugin with simple field schema and independent validation. Runtime context supplies request ID and agent identity. Fixed loopback POST, agent key only, 10-second tool HTTP bound, redirect rejection, bounded responses, no caller-set URL/actor/request ID and no lead write/approval dispatch.
- [ ] Add ignored-prose, invalid-result, wrong-context, timeout/late-result, duplicate call, and plugin tests. Add integration tests for edited approval, denial, sequential/concurrent approval, and DB/application reopen; use existing transaction seams rather than creating a parallel approval mechanism.
- [ ] Run relevant tests, inspect migration safety and structured errors, document the resulting interfaces, and commit. Task review must approve spec and quality before Task 3.

## Task 3: Dashboard proposal/recovery and reproducible acceptance

**Ownership:** new proposal client/panel in `dashboard/src`, `ChatPanel.tsx`, minimal approval refresh/status integration, browser tests, bounded synthetic test fixtures/scripts, CI additions for the new plugin/browser tests, `docs/NATIVE-CREATE-LEAD.md` and separate validation evidence/WSL handoff. Do not expand backend capabilities or enable any original profile.

**Interfaces:** Consume Task 1 runtime auth and Task 2 three-route response contract. Reuse existing `PendingApprovals` editing and decisions. Keep CRM reads separate and unchanged.

- [ ] Write browser cases for proposal pending, human-edited approval, denial, refresh/status recovery, and repeated approval. Use actual protected API/DB with a fixed synthetic model boundary where necessary; label that boundary clearly and never require a live model in CI.
- [ ] Implement explicit Propose lead mode, request ID reuse, clear pending/unknown/failure/decision display, and Check status action. Persist only the current request ID for reload recovery. Never interpret generated prose or automatically resubmit on uncertainty.
- [ ] Ensure the existing approval dialog refreshes promptly and the panel displays the stored approved result only after real approval. Agent cannot unlock as a human; unknown status remains recoverable without a new model call.
- [ ] Extend the isolated browser runner or add a small separate fixture for protected-mode proposal tests. Generate synthetic tokens, forward only required test credentials to their respective children, preserve strict process teardown and real-data isolation, and wire the tests into CI along with the new plugin suite.
- [ ] Document local setup and a WSL live acceptance handoff using separate profiles/synthetic data. First verify the prior read/scope fix, then run native create proposal, edit/approve, deny, duplicate/restart checks. Preserve existing WSL evidence and mark these live steps pending here.
- [ ] Run the full backend, both plugin suites, browser suites, and build. Record exact local results and limits, commit, and obtain task review followed by whole-branch review. Package a verified offline bundle; do not merge or push.
