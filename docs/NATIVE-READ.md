# Native OpenClaw dashboard reads

The dashboard's **CRM reads** view answers unfiltered lead-count and directory questions using an isolated native OpenClaw agent. It renders a validated backend receipt, never the model's factual prose. **General chat** remains the existing, separate workflow.

This is a narrow read implementation. The normal installer and general agent are unchanged. Without the isolated reader configuration, the view reports that native reads are not configured.

## Architecture and trust boundary

1. The browser posts its question to `POST /api/chat/directory`.
2. The backend generates a cryptographically random request ID and reserves a short-lived receipt using the plugin's gateway-authenticated HTTP route.
3. The backend calls OpenClaw `/v1/chat/completions` once, with a fresh `user` session key. It supplies **no client tools or tool_choice**, and does not run a request/finish model loop.
4. OpenClaw's isolated `native-read` agent has one native tool, `openhouse_crm`. Its schema permits only `list_lead_directory` and an empty arguments object. Its handler independently enforces that shape and the configured agent/session identity.
5. The handler issues a fixed `GET /api/leads` to the loopback CRM API. No model-controlled URL, method, operation, or actor is accepted. The backend's existing unpaginated list is counted before the first 25 rows are selected. Only ID, name, and status are returned to the model/display.
6. The handler publishes its receipt under the request ID taken from **trusted runtime session context**, not model arguments or prose. Only an existing, unexpired reservation can be completed.
7. After the agent completes, the backend consumes the receipt once, checks its request ID, operation, strict types, allowed statuses, page length, and unique IDs, and returns it to the browser. The completion text is ignored.
8. The browser clears any older result when a new request starts, validates the response shape, and renders the receipt fields as text. Failure renders a distinct error; an empty successful receipt renders zero.

The adapter uses the installed SDK's supported `registerHttpRoute({auth: "gateway"})`. Chat Completions returns generated content, while the CLI tool summary is execution metadata rather than the required typed directory receipt. The adapter is therefore a small explicit result channel, not a second tool-selection loop or transcript/prose parser. It keeps at most 128 reservations, expires them after 90 seconds, and deletes them on consumption, cancellation, or gateway shutdown. Late tool completion cannot resurrect a cancelled reservation. Receipt state is process-local and intentionally non-durable: a restart produces an error rather than an old answer.

Timeouts: 10 seconds for the tool's CRM HTTP read; 60 seconds for the backend's complete reserve/run/consume operation, plus at most 2 seconds for cleanup; 65 seconds in the browser. There are no application retries. Response size is capped at 2 MB before JSON decoding in the tool. Gateway and CRM URLs must be loopback HTTP URLs. The reader gateway must use token authentication; its token is held only by the backend. The receipt route was verified to return 401 without that token.

The English request recognizer accepts a small vocabulary for unfiltered counts and directory reads. Unknown wording, filters (status, date, location, etc.), sorting, and page requests are rejected before contacting the gateway. For example, “How many closed leads?” produces a scope error instead of the all-lead count. Use “How many leads are in the CRM?” or “Show the lead directory.” This scope check is not the security boundary: even adversarial wording can reach only the plugin's fixed read tool. Do not give the reader agent exec, filesystem, general CRM, or write tools. Do not install it in place of the normal agent or weaken the existing dashboard guards.

## Base and reuse

Base: main `eea8d87` (PR #6 already merged there). No PR #7 commits were cherry-picked.

Reused: the existing dashboard shell and chat rail, FastAPI/token middleware, SQLite fixture/schema, and the existing `/api/leads` read implementation. The tool name and simple single-operation schema follow diagnostic `67a3616`. Counting the full list before paging follows the PR #7 `list_lead_directory` semantics, without importing its broad multi-operation schema, subprocess wrapper, `crm_chat.py`, `single_action.py`, `openclaw_gateway.py`, or installer changes. The narrow plugin uses fixed HTTP transport directly instead of importing that larger runner stack.

For this view, `/api/chat/directory` replaces the old attempt to obtain factual reads through general chat or a caller-tool request/finish flow. `/api/chat` itself remains unchanged.

## Synthetic local reproduction

Requirements: the repository's Python dependencies, Node 20+, installed OpenClaw, and Ollama with the locally installed pinned model. This acceptance helper **does not install or upgrade** those tools. It creates only a separate `ohi-` profile and synthetic database.

The validated configuration is OpenClaw **2026.8.1-beta.3 (`5831b80`)**, Ollama **0.32.15**, and **`qwen3.5:9b` Q4_K_M**. Digest: `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`. Context 16384; configured maxTokens 2048; thinking off; `localModelLean: false`. Only the Ollama provider is configured and model fallbacks are explicitly empty. Do not assume other versions/models have been validated.

From this implementation checkout:

```bash
# Build the actual dashboard. No model is needed for the build.
cd dashboard
npm ci
npm run build
cd ..

# In another terminal, if Ollama is not already running:
ollama serve

# First use only: refuses to overwrite an existing profile.
python3 scripts/native_read_acceptance.py prepare
openclaw --profile ohi-dashboard-read config validate
python3 scripts/native_read_acceptance.py start
```

Use the project's virtual-environment Python in place of `python3` if needed. The helper uses the Python executable that invoked it. Keep OpenClaw and its Node runtime on PATH. Wait for `~/.openclaw-ohi-dashboard-read/gateway-stdout.log` to say `ready`, then open **http://localhost:18080** and use **CRM reads**. The default fixture contains **37 leads**, while the display page contains **25**. Ordinary count requests still return 37.

The original OpenClaw profile is not read or edited by this helper. It writes a private gateway token into the separate profile and passes it directly to the backend process environment. For a manually configured local reader, the backend settings are `NATIVE_READ_GATEWAY_URL`, `NATIVE_READ_GATEWAY_TOKEN`, and `NATIVE_READ_AGENT_ID`; use the same isolated allowlist and local-provider-only configuration as the fixture. `OHI_API_TOKEN`, if enabled on the CRM, must also be supplied to the plugin's gateway process for its fixed API read. Never expose the reader gateway token as a `VITE_` setting.

To prepare a separate empty fixture:

```bash
python3 scripts/native_read_acceptance.py prepare --profile ohi-dashboard-empty --count 0 --port 18081 --gateway-port 18881
python3 scripts/native_read_acceptance.py start --profile ohi-dashboard-empty
```

Do not run `start` twice or use occupied ports. The helper is a disposable acceptance launcher, not a service manager or installer. Existing profiles can be restarted with `start`; leave their evidence intact. Clean shutdown and fixture verification:

```bash
python3 scripts/native_read_acceptance.py snapshot
python3 scripts/native_read_acceptance.py stop
python3 scripts/native_read_acceptance.py snapshot --profile ohi-dashboard-empty
python3 scripts/native_read_acceptance.py stop --profile ohi-dashboard-empty
```

`before.json` and `after.json` contain hashes of the lead, event, pending-change, appointment, and reminder tables. Dashboard cache tables may refresh normally and are not represented as CRM record mutations. Stop Ollama separately only if this test started it. These scripts never stop the original gateway.

## Focused tests

CI runs the backend suite, the native plugin suite, the dashboard build, and the browser boundary suite. The boundary runner starts its own temporary CRM on a reserved loopback socket, disables integrations, and uses explicit environment allowlists for both the backend and browser-test processes. Gateway, provider, and CI credentials are not forwarded. The runner stops the server and removes its fixture on completion or test failure. It needs no OpenClaw or local model. After building the dashboard, run the same check locally on Linux, macOS, or WSL:

```bash
cd dashboard
npx playwright install chromium
cd ..
python3 scripts/test_native_read_browser.py
```

Use the repository virtual-environment Python if needed. `PLAYWRIGHT_MODULE` and `BROWSER_CHANNEL` remain available for an existing Playwright/browser installation. The runner always chooses its own temporary backend; an ambient `CRM_TEST_URL` does not override it.

```bash
python3 -m pytest backend/tests -q
node --test openclaw-plugins/openhouse-read/test.mjs
cd dashboard
npx playwright install chromium
CRM_TEST_URL=http://127.0.0.1:18080 npm run test:browser
LIVE_RESULTS=/tmp/ohi-native-live-new.json CRM_TEST_URL=http://127.0.0.1:18080 npm run test:live-read
EXPECTED_COUNT=0 LIVE_RESULTS=/tmp/ohi-native-empty-new.json CRM_TEST_URL=http://127.0.0.1:18081 npm run test:live-read
```

The seven browser boundary tests simulate five response/error cases; write rejection and unsupported-filter/page rejection use the actual backend route. The live script has no route mocks, uses ten fixed prompts (one for an empty fixture), records each result, and refuses to overwrite an earlier run. Set `BROWSER_CHANNEL=msedge` to use installed Edge. `PLAYWRIGHT_MODULE` optionally points to an existing Playwright installation; acceptance used bundled **1.62.1**, the same version pinned in the lockfile.

Automated test evidence and live browser evidence are separate in `docs/evidence/native-read/`. One preliminary in-app-browser smoke request also succeeded; it is not counted among the fixed ten cases. There were no failed live prompts or retries.

## Known limits and follow-ups

- Unfiltered counts and the first 25 directory rows only; no next-page control, search, per-lead detail, or history persistence in this view. Total is the full unpaginated API count, not displayed row count.
- Large directories exceeding the bounded API response produce an explicit error. This does not establish scalability.
- The receipt channel authenticates the trusted local backend/plugin path. It is not a cryptographic attestation against a compromised host or gateway.
- General chat, writes, existing integrations, fresh-machine installation, other models, and public hosting are not validated by this change. General-agent health is separate from the native reader's request outcome.
- **Before agent writes:** replace freely supplied `X-Actor` trust with an enforced identity/capability boundary. Then add one proposal-only create-lead operation, with a separately authenticated human approval endpoint and duplicate-execution protection. Test that the agent cannot claim human authority.
- **Before public alpha:** define and enforce the supported single-user local access model, including token requirements and network binding. Multi-user accounts are not required for this milestone.
- **Before claiming industry customization:** remove, configure, or explicitly scope buy/sell intent and the $750k scoring threshold. A generalized scoring engine can wait.
