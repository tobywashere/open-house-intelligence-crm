# Local experiment results — 2026-09-11

Code checkpoint: `a23d65a`, based on PR #7 revision `f015e4e`.
Branch: `codex/openclaw-single-action`. Main and PR #7 were not changed or pushed.

## Verified locally

| Check | Result |
|---|---|
| Base backend suite | 1,304 passed |
| Final backend suite | 1,345 passed, 5 existing deprecation warnings |
| Native OpenClaw plugin suite | 86 passed |
| Independent code review | Two findings fixed and re-reviewed; no open findings |
| Whitespace checks | Passed |

The 41 new tests include one-completion selection and deterministic rendering,
read-only comparison enforcement, private-report handling, and actual CRM handler
and SQLite lifecycle tests. Approve, edit, deny, repeated approval, restart,
notes, reminders, and booking conflicts were exercised with integrations off.

A scripted case demonstrates the narrow hypothesis: when the model selects the
correct directory operation but subsequently fails to finish, the existing loop
fails to produce its directory answer while the candidate renders the verified
receipt after one completion. This proves removal of that protocol dependency;
it does not measure real model success rates or latency.

Lifecycle testing also exposed the native `add_note` result's internal
`add_event` pending-operation label. The candidate bridges that one known label
before the existing strict receipt validation. Production behavior is unchanged.

The first final-suite attempt failed 14 setup tests because the new script was
not yet in HEAD. The unchanged setup integrity verifier correctly rejected an
extra source file. After a local checkpoint, the previously failing test and
the complete suite passed. No integrity check was weakened.

## Still unverified

- Exact reply and tool errors from the original WSL lead-directory failure.
- Live current-versus-candidate comparison with the same local model and hardware.
- Native plugin execution and receipt transport on that hardware.
- Natural-language proposals and approvals against a disposable configured live CRM.
- Browser interaction, backup restoration, and broader paraphrase/ambiguity evaluation.

Neither OpenClaw nor Ollama was available in this Mac shell. No live model calls,
provider deliveries, installed-agent changes, or production CRM writes were made.
The pilot is ready for the read-only hardware comparison, not production adoption.

See [operator instructions](openclaw-single-action.md). The Git bundle contains
only the new experiment commits and requires `f015e4e` in the receiving repository.
