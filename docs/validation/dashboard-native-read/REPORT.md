# Dashboard native-read implementation handoff

**Implemented and validated:** the actual dashboard can request a lead count or directory through native OpenClaw and display a verified structured result. The fixed live browser set passed **10/10**, with **zero retries** and **4.09 seconds median latency**. A separate empty fixture passed **1/1** and displayed zero, not an error.

## Architecture

`CRM reads` in the existing chat rail → `POST /api/chat/directory` → isolated native OpenClaw agent → sole fixed directory-read tool → real existing `GET /api/leads` → request-correlated one-shot receipt → backend validation → deterministic React rendering.

The backend reserves a random request ID through a gateway-authenticated plugin HTTP endpoint, then makes one Chat Completions request without client tools. The native handler obtains that ID from trusted runtime session context, performs the backend GET, and stores only the result of that execution. The backend consumes the receipt and verifies its ID, operation, strict field types, pagination consistency, and unique lead IDs. It never uses the model's completion text for facts.

The installed SDK supports `registerHttpRoute({auth: "gateway"})`; this is the small adapter used to expose typed native results. It uses an expiring, bounded in-memory map, not transcript parsing, a callback framework, or another model request/finish loop. Receipt access without a gateway token was verified to return **401**. An absent or invalid receipt is an error, not zero. Every new browser request clears the previous displayed result.

## Branch, base, and scope

- Branch: **`codex/dashboard-native-read`**.
- Base: **`eea8d87`**, the existing local main revision containing PR #6.
- Worktree: `/home/ankus/GitHub/openhouse-dashboard-read`.
- Implementation commit: `c05403dda040579fcf3044318d9f6835fdcee6b0` (code captured after validation).
- Preserved diagnostic: `codex/native-read-diagnostic` at **`67a3616`**, unchanged.
- No PR #7 commits were cherry-picked; this original implementation report preceded publication; no merge was performed.

Reused the current dashboard shell/chat rail, API middleware, SQLite/schema, and existing unpaginated lead-list endpoint. Reused the successful diagnostic's native tool name and simple single-operation schema. The “count before paging” semantics follow PR #7's `list_lead_directory`, but its broad contract, subprocess runner, request/finish machinery, and accumulated installer work were not imported. The new narrow handler directly calls the existing API.

`General chat` and `/api/chat` remain separate and unchanged in behavior. This implementation does not enable native writes, modify original dashboard tool guards, or broaden the normal agent's permissions. The existing mock badge now says **General agent · mock mode**, so it does not incorrectly describe the independent native reader.

## Files changed

- `backend/app/native_read.py`, `backend/app/routers/native_read.py`, `backend/app/main.py`: read service, strict receipt validation, timeout/error handling, endpoint registration.
- `openclaw-plugins/openhouse-read/{index.js,read.js,openclaw.plugin.json,package.json}`: one native read capability and gateway-authenticated receipt adapter.
- `dashboard/src/components/{ChatPanel.tsx,LeadDirectoryChat.tsx,LocalBadge.tsx}`, `dashboard/src/nativeRead.ts`: dedicated read view, deterministic count/directory display, current-request-only state, error/empty states.
- `backend/tests/test_native_read.py`, plugin `test.mjs`, and `dashboard/tests/native-read.{browser,live}.cjs`: focused boundary and real browser acceptance checks.
- `dashboard/package.json`, `dashboard/package-lock.json`: two test commands and **Playwright 1.62.1**, with unrelated lockfile changes removed.
- `.env.example`, `docs/NATIVE-READ.md`: opt-in reader settings, design, reproduction, limits, and required follow-ups.
- `scripts/native_read_acceptance.py`, `scripts/native_read_server.py`: synthetic acceptance fixture launcher and metadata-only backend request observation. These are not a production installer.
- `docs/evidence/native-read/`: sanitized results and screenshots.

## Automated verification — separate from live evidence

| Check | Result |
|---|---|
| Full backend suite | **616 passed** in 25.11 seconds; five existing framework deprecation warnings |
| Included new backend cases | **19 passed**: zero/non-empty/paginated results, ignored model prose, malformed/missing/stale receipts, unavailable/failed gateway, timeouts, duplicate rows, write attempts, safe route errors |
| Native plugin tests | **4 passed**: identity/session binding, one-shot receipts, empty/failure/expiry/cancellation, no write or arbitrary-URL dispatch, late-result cancellation |
| Browser boundary suite | **6 passed**: correct paginated count, empty state, unavailable error, malformed response, failed new request clears old result, real backend write rejection |
| TypeScript + Vite production build | **Passed** |
| Installed OpenClaw profile validation | Both isolated profiles **passed** |
| Diff whitespace check | **Passed** |

Five browser boundary cases mock the HTTP response deliberately. The write-rejection browser case uses the actual backend. These results are not represented as live model successes.

## Real browser/OpenClaw acceptance

The committed live script used ten fixed prompts, a fresh backend-generated OpenClaw session for every request, and no interception/mocks. It exercised the built dashboard and actual backend route. Results are in `live-browser.json`.

- **10/10** verified counts and directory pages, **0 failures**, **0 retries**.
- Expected and returned total: **37**; displayed/model page: **25**. This explicitly tests that displayed row count is not mistaken for total count.
- Per-request latency range: **3.124–4.877 seconds**; median **4.090 seconds**.
- Ten application completion requests; **twenty internal inference rounds**.
- Each trace shows **one native tool, zero client tools**, one `openhouse_crm` call with `{"operation":"list_lead_directory","arguments":{}}`, and one successful backend `GET /api/leads` with the matching request ID.
- Each browser receipt exactly matches the native handler result; the visible request ID matches the receipt ID.
- A separate **zero-lead** fixture passed **1/1**, 3.823 seconds, two inference rounds. Screenshot shows **0 leads total / No leads in the CRM**.
- One earlier in-app-browser smoke request also succeeded. It is explicitly outside the fixed ten, not a hidden retry.
- Generated descriptions, timestamps, scores, and timezone annotations are not rendered. Only validated total, name, ID, and raw CRM status are displayed.

`dashboard-live.png` and `dashboard-empty.png` show the actual rendered views. `live-browser.json` and `live-empty.json` include per-case handler and backend evidence; `runtime.json` records the model and receipt-auth check. All records are synthetic.

## Exact tested runtime

- OpenClaw **2026.8.1-beta.3 (`5831b80`)**.
- Ollama **0.32.15**.
- Model **`ollama/qwen3.5:9b`**, **Q4_K_M**, digest `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
- Context **16384**; configured maxTokens **2048**; thinking **off**; localModelLean **false**.
- Native profile contains only Ollama and explicit **empty model fallbacks**. Gateway processes receive a clean environment without inherited cloud credentials.
- WSL Node **v24.15.0**, Python **3.14.4**. Browser tests used installed Microsoft Edge headless with bundled **Playwright 1.62.1**. The same Playwright version is pinned for reproduction.

## Exact local restart/reproduction

In WSL, use the retained fixture and profile; do **not** rerun `prepare` over them:

```bash
cd /home/ankus/GitHub/openhouse-dashboard-read
export PATH=/home/ankus/.openclaw/tools/node-v24.15.0/bin:/home/ankus/.local/bin:/usr/local/bin:/usr/bin:/bin
# Start Ollama in another terminal if it is stopped:
ollama serve
# In this checkout's terminal:
/home/ankus/GitHub/open-house-intelligence-crm/.venv/bin/python scripts/native_read_acceptance.py start
```

Wait for `~/.openclaw-ohi-dashboard-read/gateway-stdout.log` to say `ready`. Open **http://localhost:18080** → **CRM reads** → ask for the count or directory. The built dashboard is retained. For a fresh build use `cd dashboard && npm ci && npm run build`; no upgrades were performed during implementation.

The analogous empty fixture starts with the same Python command plus `--profile ohi-dashboard-empty` and opens on **http://localhost:18081**. Full first-use setup, browser commands, new-result filenames, and shutdown commands are in `docs/NATIVE-READ.md`.

The actual automated browser invocation used the bundled Windows Node executable with `PLAYWRIGHT_MODULE` pointing at the bundled Playwright package, `BROWSER_CHANNEL=msedge`, `CRM_TEST_URL=http://127.0.0.1:18080`, and `LIVE_RESULTS` set to a new Windows evidence file. The same scripts can run through `npm run test:browser` and `npm run test:live-read` with the declared dependency and an installed Playwright browser.

## Preservation and final process state

- Both fixtures' CRM content hashes match before/after: all lead, event, pending-change, appointment, and reminder rows are unchanged. Ordinary dashboard cache refreshes are outside those CRM record hashes.
- All **24 original-file hashes match**, including the original CRM database, active OpenClaw configuration/backups, and installed CRM skills. Original source/worktree changes and diagnostic evidence were retained.
- Created only the separate `ohi-dashboard-read` and `ohi-dashboard-empty` profiles, synthetic databases, and private profile tokens. No machine-wide settings, original profile policies, or integrations were changed.
- Both acceptance gateways, both fixture backends, and the Ollama process started by this task were **stopped**. The original gateway was **active on 18789** at final verification; the original CRM backend remained stopped.
- Private profile configuration and runtime logs remain in those isolated profile directories; they are not committed. Sanitized artifacts were checked against the active and fixture gateway secrets before commit.

## Limits and smallest next task

This validates unfiltered counts and the first 25 directory rows on this one local runtime/model. It does not validate broader tools, saved read-chat history, paging controls, writes, fresh-machine installation, public access, or other models. Missing configuration and execution failures remain explicit errors.

**Next, before introducing any agent write:** enforce a capability/identity boundary that cannot be bypassed by supplying `X-Actor`. Then implement one proposal-only create-lead operation, with human-only approval and idempotency/duplicate-execution tests. Do not extend this read agent's permissions to get there.

Before public alpha, define and enforce the single-user local access model, token requirements, and network binding. Before claiming industry customization, configure or clearly scope the buy/sell intent and $750k scoring assumptions. These remain follow-ups, not changes in this implementation.
