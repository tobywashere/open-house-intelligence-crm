# Review fixes: request scope and CI coverage

Follow-up to review of `186335fba6db921398cb63ce8259fd4fbcf74614`, validated locally on 2026-09-20. The original WSL evidence remains unchanged and describes the earlier implementation.

## Behavior changes

- Questions outside the narrow unfiltered count/directory vocabulary return `unsupported_request` before gateway configuration or any model/tool call. This includes status, date, location, budget, sorting, individual-record, and later-page requests. The error provides supported example questions.
- The dashboard states the unfiltered first-page scope before submission and alongside a successful result.
- All ten original live-acceptance prompts, plus common unfiltered variants, remain accepted by the scope guard. This compatibility check used simulated gateway responses; it is not a new live-model run.
- CI now runs the native plugin tests and browser boundary tests as well as the existing backend suite and dashboard build. Live-model acceptance remains a separate manual check.
- `scripts/test_native_read_browser.py` runs the browser boundary suite against a fresh temporary database. Its backend inherits only runtime path/locale variables and uses mock agent mode and disabled integrations. The browser-test child also receives only explicit runtime/browser settings. Neither child receives ambient gateway, provider, or CI credentials. A reserved socket prevents accidental reuse of a real CRM server. The runner propagates test failures and cleans up its process and database.

## Validation

| Check | Result |
| --- | --- |
| Regression tests before the scope fix | 14 new tests failed as expected; filtered questions reached the gateway/configuration path |
| Focused native-read backend tests after the fix | 47 passed |
| Full backend suite | 644 passed; five existing deprecation warnings |
| Native plugin suite | 4 passed |
| Browser boundary suite through the new CI runner | 7 passed; five simulated response cases, two real backend rejection cases |
| TypeScript/Vite build | Passed |
| Deliberate browser-start failure | Failure propagated, all child processes stopped, temporary fixture directory removed |
| Credential-canary check at child-process invocation | Failed before browser environment isolation; passed afterward for backend and browser, with documented browser settings retained and the ambient CRM URL replaced |
| Workflow YAML and diff whitespace | Parsed successfully; no whitespace errors |

The regression tests include a mixed-status CRM with one new and one closed lead; asking for closed leads now returns a scope error instead of the total of both leads. Browser cases verify status, date, location, and pagination rejections through the real backend.

Commands from the implementation checkout:

```bash
python -m pytest backend/tests -q -p no:cacheprovider
node --test openclaw-plugins/openhouse-read/test.mjs
cd dashboard && npm run build && cd ..
python scripts/test_native_read_browser.py
```

Local runtime: repository Python 3.13.7, Node 22.19.0, bundled Playwright 1.62.1/Chromium. The build reused existing dependencies whose installed versions match the lockfile; a fresh dependency download had timed out during review. GitHub-hosted CI has not been run for these changes. No OpenClaw/Ollama inference was rerun on this Mac.

The scope guard deliberately prefers a clear rejection of unfamiliar wording over guessing. It does not add filtering, pagination, writes, or new agent permissions. Agent identity/capability enforcement remains the prerequisite for a future proposed-write workflow.
