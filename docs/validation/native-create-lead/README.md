# Native create-lead validation

These are sanitized local Mac transcripts for Task 3 on top of
`75371fa49ddfe211335be1d579f4b3f11051d3bc`. The source revision is the commit
containing these artifacts. [provenance.json](provenance.json) records exit codes,
source hashes, log hashes and boundaries. Worktree, user-home and OS temporary
paths were replaced; outcomes and summaries were preserved.

## Results actually obtained

| Check | Result | Transcript |
| --- | --- | --- |
| Final full backend | 724 passed, 5 existing deprecation warnings | [backend-final.txt](backend-final.txt) |
| Both native plugins | 50 passed | [plugins-full.txt](plugins-full.txt) |
| Proposal browser | 10 passed | [proposals-final.txt](proposals-final.txt) |
| Auth browser | 3 passed | [auth-browser.txt](auth-browser.txt) |
| Read browser | 7 passed | [read-browser.txt](read-browser.txt) |
| TypeScript + Vite build | passed | [close-build.txt](close-build.txt) |
| Final helper safety/CLI tests | 5 passed | [helper-final.txt](helper-final.txt) |
| Close tests after orphan-row assertion strengthening | 8 passed | [close-final.txt](close-final.txt) |
| Legacy setup suite with capability guard | 110 passed | [setup-guard-green.txt](setup-guard-green.txt) |

The earlier full backend run had 722 tests and preceded the helper read-profile
and CLI tests. The final 724-test run includes them. Only an assertion in the
close-race test changed afterward; its eight focused close cases were rerun.
Every listed command was collected through its terminal exit code. The five Python
warnings are existing FastAPI lifecycle/python-multipart deprecations.

## Commands and environment

Executed from the implementation worktree using its parent project's matching
virtual environment (`../../.venv/bin/python`) and matching dashboard dependency
installation. The temporary dashboard/node_modules symlink was removed before
commit. No local OpenClaw or Ollama was installed or run.

```bash
../../.venv/bin/python -m pytest backend/tests/ -q
../../.venv/bin/python -m pytest backend/tests/test_native_proposals.py -q -k close
../../.venv/bin/python -m pytest backend/tests/test_native_proposal_acceptance_helper.py -q
../../.venv/bin/python -m pytest backend/tests/test_setup_openclaw.py -q
node --test openclaw-plugins/openhouse-read/test.mjs openclaw-plugins/openhouse-proposals/test.mjs
npm --prefix dashboard run build
```

Each browser command used the preinstalled Playwright module via
`PLAYWRIGHT_MODULE=<user-home>/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright`:

```bash
../../.venv/bin/python scripts/test_native_read_browser.py --suite auth
../../.venv/bin/python scripts/test_native_read_browser.py --suite read
../../.venv/bin/python scripts/test_native_read_browser.py --suite proposals --screenshots /tmp/native-create-lead-task3/screenshots
```

These final screenshots show only synthetic data: [empty runtime unlock](unlock.png),
[populated proposal](proposal.png),
[review](review.png), [approved](approved.png). The approved result is the real
stored lead; surrounding dashboard aggregate cards have their own refresh timing.

## TDD and intermediate runs

- [Proposal RED](proposals-red.txt): eight cases failed because the mode was absent.
  [Initial GREEN attempt](proposals-green.txt) exposed an ambiguous test locator
  for two uncertainty messages, then a leftover pending row from the interrupted
  test blocked following cases. Correcting the locator and using explicit recovery
  produced [8 passing cases](proposals-green-2.txt).
- [Close API RED](close-red.txt): eight failures before the close route/function
  existed. [GREEN](close-green.txt) proves tombstones, late work, concurrency,
  role checks, exact received text conflicts and restart behavior.
- [Close UI RED](close-browser-red.txt): the existing eight cases passed and the
  two new close cases failed on the missing control. [GREEN](close-browser-green.txt)
  passed all ten, including lost close response and a proposal winning the race.
- [Helper RED](helper-red.txt), [GREEN](helper-green.txt); then
  [read-selector RED](read-helper-red.txt) and [final helper checks](helper-final.txt).
- [Legacy guard RED](setup-guard-red.txt): four cases proved existing setup still
  accepted capability credentials. [GREEN](setup-guard-green.txt) includes the
  complete 110-test setup regression suite.

For focused RED commands use the same commands above with `-k close`,
`-k capability_mode_blocks` (setup test file), or `-k read_acceptance` (helper test
file); browser RED used the same `--suite proposals` runner before implementation.

## Boundary and pending live work

**Proposal inference was simulated.** The fixture overrides only the async
completion seam. It posts fixed synthetic fields to the real agent HTTP endpoint
with a generated agent key. The human browser gets a separate generated key.
Reservation, role enforcement, SQLite, pending edits/approval/denial, replay and
close are real application code. No direct pending/lead DB insertion is used by
that browser fixture. Error-response substitutions for browser recovery are named
in the tests. Auth tests substitute endpoint responses to control 401 ordering.

This is not live native inference evidence. Historical c05403d read results are
unchanged. **720cf52 read/scope-fix WSL acceptance and new native proposal WSL
acceptance remain pending.** Follow [the isolated WSL handoff](../../NATIVE-CREATE-LEAD.md)
with the pinned already installed runtime, first the read checks, then proposals.
No original profiles, data, machine services, credentials, runtime logs or DB files
are included here. No push, merge or bundle creation was performed by Task 3.
