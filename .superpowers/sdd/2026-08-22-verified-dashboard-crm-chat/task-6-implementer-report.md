# Task 6 Implementer Report: Capability-Based OpenClaw Setup

Date: 2026-08-23

## Status

Implemented capability-based setup verification for the canonical CRM contract,
the exact native tool and required hooks, the dashboard channel block, and
request-scoped Chat Completions tools. Setup remains fail-closed and preserves
the existing dedicated-agent policy.

## Implementation

- Validates the canonical `skills/crm-db-operations/contract.json` as bounded,
  UTF-8, duplicate-key-free version-1 JSON before mutation, records its SHA-256,
  and verifies byte-identical digest after skill synchronization.
- Requires the installed CRM skill to contain that contract and no stale
  `operations.json`.
- Keeps rollback snapshots of all four owned installed skill trees until every
  late runtime capability check succeeds. Later failure restores previous
  skill files alongside the existing agent-field, plugin, plugin-allowlist,
  and executable-approval rollback. If the Gateway had already restarted,
  rollback restarts it again after restoration.
- Validates authoritative plugin runtime inventory for exactly one non-optional
  `openhouse_crm` tool and exactly these hooks when hook names are exposed:
  `before_tool_call`, `after_tool_call`, `reply_payload_sending`, and
  `gateway_stop`. Conflicting or duplicate inventories fail closed.
- When OpenClaw cannot enumerate hook names, uses only a 30-second, 256-KiB,
  loopback-only `/tools/invoke` diagnostic under `openhouse-dashboard`. The
  operation identifier is deliberately absent from the canonical contract, so
  even a missing block cannot dispatch a supported CRM operation.
- Proves Chat Completions request-scoped function support with one fixed nonce
  function and `tool_choice: "required"`. The request runs under the dashboard
  channel, contains no CRM/REST/shell/file/database tool, has no user-provided
  content, and only accepts an exact single nonce-bearing tool call. HTTP 400,
  authentication failure, provider/model unavailability, and malformed output
  all fail setup; unavailable providers never produce success.
- Redacts CRM and Gateway token/password environment values from rendered setup
  output.
- Dry run reports contract installation, stale-catalog cleanup, runtime hook
  checks, and the bounded client-tool probe without performing file, config, or
  network mutations.
- Does not add a version gate or alter global configuration, unrelated agents,
  the exact tool allow/deny/exec policy, or the sole daily-brief approval.

The runtime proof follows the current documented `plugins inspect --runtime
--json` tool/hook inventory. That inventory does not expose function parameter
schemas; exact schema provenance is instead established by the verified linked
repository plugin, whose runtime builds its parameters from the same validated
canonical contract. The plugin regression suite pins that construction.

## TDD Evidence

### Baseline

```text
PYTHONPATH=.. .../.venv/bin/pytest tests/test_setup_openclaw.py tests/test_launchers.py -q
206 passed, 5 warnings
```

### RED

After adding fresh-install, rerun, upgrade, contract-tamper, hook-inventory,
fallback-diagnostic, client-capability, rollback, dry-run, redaction, and HTTP
payload tests, the focused suite failed for the intended missing behavior:

```text
13 failed, 207 passed, 5 warnings
```

### Final GREEN

Required focused suite:

```text
PYTHONPATH=.. /Users/johaanmannanal/Documents/GitHub/open-intelligence-crm/.venv/bin/pytest tests/test_setup_openclaw.py tests/test_launchers.py -q
222 passed, 5 warnings
```

Fresh relevant setup/doctor/plugin-consumer suite:

```text
PYTHONPATH=.. /Users/johaanmannanal/Documents/GitHub/open-intelligence-crm/.venv/bin/pytest tests/test_setup_openclaw.py tests/test_launchers.py tests/test_doctor.py tests/test_openclaw.py tests/test_crm_chat.py tests/test_crm_operation_catalog.py -q
363 passed, 5 warnings
```

Bundled plugin regression suite:

```text
npm test
48 passed, 0 failed
```

Additional final checks:

- `python -m py_compile` on the setup script and both changed test modules:
  passed.
- `git diff --check`: passed.
- The five Python warnings are pre-existing Starlette/FastAPI deprecations.

## Files Changed

- `scripts/setup_openclaw.py`
- `backend/tests/test_setup_openclaw.py`
- `backend/tests/test_launchers.py`
- `.superpowers/sdd/2026-08-22-verified-dashboard-crm-chat/task-6-implementer-report.md`

## Self-review

### Capability and false-success posture

- Setup succeeds only after exact contract, runtime tool, hook/block, policy,
  approvals, and real client-tool response checks pass.
- A provider error or model response without the exact single dummy function
  call is explicitly unproven, never treated as success.
- Runtime inventories are validated by content, not inferred from an OpenClaw
  version number.

### Probe safety

- The model probe exposes only a nonce echo function; it cannot execute that
  client-side function at the Gateway.
- The probe uses the synthetic dashboard marker, so the verified plugin hook
  blocks internal CRM execution. Existing agent policy denies file tools and
  constrains host exec to the sole daily-brief approval; the fixed prompt
  contains no command or path.
- The fallback hook diagnostic is loopback-only and uses a non-operation absent
  from the canonical contract. Both HTTP paths have strict time and response
  size bounds.

### Rollback and scope

- Existing owned skill trees are copied to a private temporary snapshot before
  replacement and restored byte-for-byte after a late capability failure.
- The prior agent policy, plugin source/enablement, plugin allowlist, and
  executable approvals remain covered by the rollback transaction.
- No broad plugin policy, unrelated agent, database, or application API state
  is changed.

## Concerns / Residual Risk

- The repository has no live OpenClaw Gateway fixture. HTTP request formation,
  response validation, runtime-inventory compatibility, fail-closed behavior,
  and rollback are covered with fakes shaped from the authoritative runtime
  contract; a real installation is intentionally accepted only when its own
  runtime inventory and bounded live probes prove the capabilities.
- OpenClaw runtime inspection currently exposes tool names/optionality and hook
  names, but not the registered JSON parameter schema or hook matchers. Setup
  therefore combines authoritative runtime registration with canonical linked
  source/digest verification and the bundled plugin tests instead of claiming
  unsupported introspection.

## Fix Round 1 Continuation

The interrupted fix round was resumed without discarding its intentional
worktree edits. Its reproduced focused baseline was:

```text
55 failed, 193 passed, 5 warnings
```

The continuation closed the compatibility and transaction gaps exposed by that
baseline:

- Normalized process-owned temporary roots to their strict resolved paths, so
  macOS `/var` symlinks do not trip the no-follow policy.
- Allocates a unique diagnostic agent before the first target mutation, records
  it as absent in the recovery manifest, proves it is unbound, queries its
  effective tools, and permits the client-function probe only when its grouped
  tool inventory is empty. The diagnostic agent is deleted and verified absent
  before a second success-path Gateway restart unloads it.
- Runs the safe dashboard-block behavior diagnostic even when hook names are
  enumerable and requires the exact forbidden response and block reason.
- Rejects non-loopback or ambiguous diagnostic URLs before any authenticated
  HTTP request, including URLs with credentials, paths, query strings, or
  fragments.
- Captures the canonical contract bytes, identity, and digest before mutation;
  installs those captured bytes; then re-verifies both source and installed
  digest immediately before runtime probes.
- Makes the recovery manifest complete before mutation, including Gateway env
  bytes/mode/absence, config presence and values, CRM and diagnostic agents,
  bindings, approvals, owned skills, plugin allowlist, and plugin
  source/enablement.
- Restores and authoritatively verifies rollback state in this order:

  1. Diagnostic agent and its private workspace.
  2. Legacy token config.
  3. Gateway approvals as the exact previous set.
  4. Bindings as the exact previous value or absence.
  5. Token SecretRef and CRM URL config in reverse mutation order.
  6. Existing CRM agent managed policy exactly, or deletion of a newly created
     CRM agent.
  7. `plugins.allow` as the exact previous value or absence.
  8. Plugin source registration and enabled/disabled state.
  9. All four owned skill trees.
  10. Gateway `.env` bytes, mode, or prior absence.
  11. After any attempted restart, restart the restored Gateway and verify all
      restored state again from authoritative reads.

If any rollback action or verification is incomplete, the private backup is
retained and setup emits a two-line safe recovery path. Otherwise the backup is
discarded. Regression coverage includes partial plugin installation and
enablement, implicit allowlist mutation on success and failure, authoritative
agent readback, post-rollback-restart corruption, bound diagnostic agents,
non-empty effective tools, and the complete recovery manifest.

### Continuation Verification

Required focused suite:

```text
PYTHONPATH=.. /Users/johaanmannanal/Documents/GitHub/open-intelligence-crm/.venv/bin/pytest tests/test_setup_openclaw.py tests/test_launchers.py -q
255 passed, 5 warnings
```

Broader relevant suite:

```text
PYTHONPATH=.. /Users/johaanmannanal/Documents/GitHub/open-intelligence-crm/.venv/bin/pytest tests/test_setup_openclaw.py tests/test_launchers.py tests/test_doctor.py tests/test_openclaw.py tests/test_crm_chat.py tests/test_crm_operation_catalog.py -q
396 passed, 5 warnings
```

Bundled plugin suite:

```text
npm test
48 passed, 0 failed
```

`python -m py_compile` for the setup script and both changed test modules and
`git diff --check` also pass. The five warnings remain the pre-existing
Starlette/FastAPI deprecations.

The residual integration concern is unchanged: the repository has no live
OpenClaw Gateway fixture. The tests use authoritative-shaped fakes, while a real
installation remains fail-closed unless its own runtime reads and bounded live
probes establish the required behavior.

## Fix Round 2

This round addressed only the four remaining review findings.

### TDD / RED evidence

Focused regressions were added before production changes for:

- `OSError` from rollback plugin install, enable, disable, and uninstall, plus
  the restored-Gateway restart. The unguarded calls escaped `configure_openclaw`
  instead of completing skill/env restoration and recovery reporting.
- boolean contract versions and exponent-overflow schema bounds. `version:true`
  and nested `1e1000`/`-1e1000` values were accepted by the prior validator.
- POST 301, 302, 303, 307, and 308 responses for both authenticated probes,
  using only two temporary loopback listeners. Outside the listener-restricted
  sandbox, the prior default urllib opener followed 301/302/303 to the second
  listener and returned its 200 response (`7 failed, 3 passed`).
- backup deletion failure and partial deletion. The regressions model
  `ignore_errors=True` suppressing cleanup failure and require a failed result,
  retained private path, safe recovery/cleanup guidance, and no secret output.

The initial combined focused run recorded `20 failed, 4 passed`; ten listener
cases were separately rerun with loopback permission so sandbox denial was not
mistaken for product RED evidence.

### Minimal GREEN changes

- Every direct rollback plugin mutation and the rollback restart now catches
  `OSError` independently, records the failed restoration, and continues through
  later plugin verification, skill restoration, Gateway env restoration, and
  recovery-path reporting.
- Contract version is accepted only when it is the exact non-boolean integer
  `1`. Schema validation recursively rejects non-finite decoded floats, while
  finite IEEE-754 boundary values remain accepted.
- Both authenticated probes share an explicit no-redirect urllib opener. Every
  3xx response is returned as an unsupported probe result without following its
  `Location`; real-server tests prove the destination receives zero requests.
- Private snapshot cleanup no longer ignores errors. Setup requires both
  deletion and authoritative `lstat` absence verification before success. A
  deletion error or remaining node triggers rollback, retains/reports the
  private path, and includes exact-directory recovery and cleanup guidance.

### Fix-round-2 verification

New focused regressions:

```text
24 passed
```

Required focused setup/launcher suite:

```text
PYTHONPATH=.. /Users/johaanmannanal/Documents/GitHub/open-intelligence-crm/.venv/bin/pytest tests/test_setup_openclaw.py tests/test_launchers.py -q
280 passed, 5 warnings
```

Broader relevant Python suite:

```text
PYTHONPATH=.. /Users/johaanmannanal/Documents/GitHub/open-intelligence-crm/.venv/bin/pytest tests/test_setup_openclaw.py tests/test_launchers.py tests/test_doctor.py tests/test_openclaw.py tests/test_crm_chat.py tests/test_crm_operation_catalog.py -q
421 passed, 5 warnings
```

Bundled plugin suite:

```text
npm test
48 passed, 0 failed
```

`python -m py_compile` for the setup script and both changed test modules and
`git diff --check` pass. The five Python warnings remain the pre-existing
Starlette/FastAPI deprecations.

### Fix-round-2 self-review

- Authenticated urllib use exists only in `_post_gateway_json`; it always builds
  the explicit no-redirect opener, and both probe methods route through it.
- Rollback helpers already convert their own `OSError`/`SetupConflict` failures
  to false/error results. The previously direct plugin and restart mutations now
  have local exception boundaries, so no rollback CLI action can skip later
  skill/env restoration or final recovery reporting.
- Cleanup failure keeps the agent rollback snapshot live until deletion is
  verified, so a late cleanup failure still enters the full reverse transaction.
- No plugin runtime, CRM API, database, or policy surface was broadened.

Residual concern remains limited to live OpenClaw integration: the repository
has no real Gateway fixture, so real installations remain fail-closed behind
their authoritative inventory and bounded loopback probes.
