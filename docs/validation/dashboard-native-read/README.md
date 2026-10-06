# Dashboard native-read review evidence

Implementation/code tested: **c05403dda040579fcf3044318d9f6835fdcee6b0**.
Direct parent/base: **eea8d87ef0c8ce12907590be64d82c5807da248a**.
Branch: `codex/dashboard-native-read`; origin: `https://github.com/tobywashere/open-house-intelligence-crm.git`.

These are retained results from validation of the implementation working tree captured in that commit. This separate documentation commit packages evidence only; it does not claim a new test run against the documentation HEAD. The original implementation commit is unchanged.

## Review map

- [Implementation report](REPORT.md): architecture, scope, results, limits, and local restart commands. Publication status in the original report is historical.
- [Reproduction guide](../../NATIVE-READ.md): fresh synthetic fixture preparation, start/stop, build, browser commands, and constraints.
- [Ten live browser requests](live-browser.json) and [empty CRM](live-empty.json): individual receipts, native tool calls, request-correlated backend GETs, expected counts, timings, and summaries. All displayed CRM records are synthetic.
- [Automated summary](automated-tests.json) and [backend output](backend-tests.txt).
- [Runtime](runtime.json): OpenClaw version, Ollama server version, model tag/digest/quantization/context, and unauthenticated receipt HTTP 401 check.
- [Redacted configuration](agent-config.redacted.json) and [agent instructions](agent-instructions.txt): copied from the retained acceptance profile, with token/API-key values removed and paths normalized to `${REPO}` and `${PROFILE}`. This is a review example, not a directly usable credential-bearing config. The empty profile uses CRM port 18081 and gateway port 18881 instead of 18080/18879.
- [Populated screenshot](dashboard-live.png), [empty screenshot](dashboard-empty.png), and both `*-preserved.json` files document rendering and unchanged fixture CRM content hashes.
- [Provenance/checksums](provenance.json) identifies the tested code and retained artifacts. Original-file preservation manifests and process-state evidence remain in [the original evidence directory](../../evidence/native-read/).

## Automated checks versus live acceptance

| Check | Reproduction command (from repository root unless noted) | Retained result |
| --- | --- | --- |
| Backend | `PYTHONDONTWRITEBYTECODE=1 python -m pytest backend/tests -q -p no:cacheprovider` | 616 passed, including 19 new cases; 25.11 s; five existing deprecation warnings |
| Native plugin | `node --test openclaw-plugins/openhouse-read/test.mjs` | 4 passed |
| Browser boundary | In `dashboard`: `CRM_TEST_URL=http://127.0.0.1:18080 BROWSER_CHANNEL=msedge npm run test:browser` | 6 passed; five mocked response cases, one actual backend write-rejection case |
| Build | In `dashboard`: `npm run build` | TypeScript and Vite passed |
| Profile configuration | `openclaw --profile ohi-dashboard-read config validate` and corresponding `ohi-dashboard-empty` command | Both passed |

The backend command used `/home/ankus/GitHub/open-house-intelligence-crm/.venv/bin/python`; WSL Python was 3.14.4 and Node was v24.15.0. Browser acceptance ran on Windows using the bundled Node/Playwright 1.62.1 installation with `PLAYWRIGHT_MODULE` set to that package and `BROWSER_CHANNEL=msedge`. The commands above are portable equivalents of the retained invocations; the original backend command is also recorded in JSON. Browser scripts were invoked directly with Node (`node --test dashboard/tests/native-read.browser.cjs` and `node dashboard/tests/native-read.live.cjs`).

Live reproduction, with the corresponding isolated fixture already started (see reproduction guide), from `dashboard`:

```bash
BROWSER_CHANNEL=msedge CRM_TEST_URL=http://127.0.0.1:18080 LIVE_RESULTS=/tmp/native-read-review-new.json npm run test:live-read
BROWSER_CHANNEL=msedge EXPECTED_COUNT=0 CRM_TEST_URL=http://127.0.0.1:18081 LIVE_RESULTS=/tmp/native-empty-review-new.json npm run test:live-read
```

The populated live set passed **10/10**, zero retries, median **4090 ms**, total **37**, first page **25**. The empty fixture passed **1/1**, **3823 ms**, total zero. Each application request used one native tool, zero client tools, and two internal inference rounds. These live results use the actual built dashboard, backend, OpenClaw, Ollama, and synthetic CRM API. They are separate from mocked browser boundary tests. One preliminary in-app-browser smoke success is outside the fixed ten and was not a retry.

Exact model: `ollama/qwen3.5:9b`, Q4_K_M, 9.7B, digest `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`. OpenClaw: `2026.8.1-beta.3 (5831b80)`. Ollama client/server: `0.32.15`. Context 16384, configured maxTokens 2048, thinking off, localModelLean false, no model fallbacks.

## Manual setup and evidence limits

- OpenClaw, Ollama, and the model were already installed. Acceptance reused an existing Python environment and dashboard dependencies (a temporary dependency symlink was removed before the implementation commit). It did not validate a clean installation or perform upgrades. `npm ci` in the reproduction guide is a fresh setup instruction, not a claimed acceptance step.
- Separate synthetic fixture profiles and private gateway tokens were prepared. Ollama and isolated gateways/backends were explicitly started, and readiness was checked. Existing profiles must be restarted with `start`, not overwritten with `prepare`.
- Automated browser setup suppressed the daily-summary overlay through local storage; the preliminary interactive smoke required dismissing it manually. Screenshots were visually inspected.
- Raw credential-bearing profile files, gateway/session logs, runtime databases, and original CRM rows are deliberately excluded. The browser/plugin/build results are retained summaries; full console transcripts for those checks and an exact Edge build number were not retained. Backend console output and per-request live structured evidence are included.
- Scope is unfiltered count and first 25 rows on this one runtime/model. No pagination control, general-chat acceptance, other-model acceptance, fresh-machine installation, deployment, or write workflow is claimed. Fixture record hashes exclude routine dashboard caches.

## Publication review

All 34 implementation-changed files were checked for known retained credentials, original CRM name/email/phone/notes values, common secret-key patterns, database signatures, and accidental runtime file types. No matches requiring removal were found. Both screenshots show only synthetic fixtures. Existing preservation manifests contain paths and hashes, not original CRM records or configuration credentials. Added review artifacts were checked again. No implementation code, main branch, or PR #7 is changed by this evidence packaging.
