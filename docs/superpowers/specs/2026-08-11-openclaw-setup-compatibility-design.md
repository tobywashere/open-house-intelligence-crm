# OpenClaw Setup Compatibility Design

**Date:** 2026-08-11

## Problem

The setup helper can reject a compatible OpenClaw installation during a fresh
install for two confirmed reasons:

1. Its help parser stops at a nested `Examples:` heading inside the `Commands:`
   section, so commands printed after that example can be reported as missing.
2. It requires `agents.list` to exist before creating the dedicated CRM agent.
   A fresh configuration can contain only `agents.defaults`, so OpenClaw
   correctly reports that the requested roster path is missing.

The surrounding audit found a related compatibility risk. Current OpenClaw
guidance prefers the keyed `agents.entries` roster while older installations
use the array-based `agents.list` roster. The helper currently understands and
writes only the legacy array form.

## Goals

- Let a supported fresh OpenClaw install create the dedicated CRM agent.
- Support both modern `agents.entries` and legacy `agents.list` rosters.
- Keep all existing approval, tool restriction, SecretRef, and post-write
  verification guarantees.
- Fail before mutation when output is malformed, contradictory, or unsafe.
- Keep the change limited to the setup helper, its tests, and operator docs.

## Non-goals

- Do not edit OpenClaw configuration files directly.
- Do not guess a roster schema from an OpenClaw version string.
- Do not weaken the dedicated agent's exec-only policy or human approval flow.
- Do not claim live provider or hardware verification from automated tests.

## Chosen Approach

Add a small compatibility adapter at the OpenClaw CLI boundary.

The adapter reads `openclaw config get agents --json` instead of requiring the
optional `agents.list` child path. It normalizes one of three states:

- modern keyed roster at `agents.entries`;
- legacy array roster at `agents.list`;
- no explicit roster yet on a fresh install.

It returns normalized agent records plus the exact configuration prefix for a
selected agent. For example:

- `agents.entries["openhouse-crm"]` for a modern roster;
- `agents.list[1]` for a legacy roster.

If no roster exists before creation, real setup runs `openclaw agents add`,
reads the root again, and uses the schema OpenClaw actually wrote. Dry-run mode
does not invent a schema. It describes the planned restrictions by agent ID
and states that the exact roster path will be selected after agent creation.

The old symptom-only alternative was rejected because it would accept a
missing `agents.list` and then fail on installations that create
`agents.entries`. Direct JSON-file editing was rejected because it bypasses
OpenClaw's validated write path.

## Help Parsing

Command discovery remains strict but becomes indentation-aware:

1. Find the `Commands:` heading.
2. Treat the least-indented valid command row beneath it as the direct child
   indentation.
3. Collect command tokens only from rows at that indentation.
4. Ignore deeper nested headings and example text.
5. Stop at the next section heading at the same or shallower indentation as
   `Commands:`.

Option discovery continues to use exact long-option tokens across the complete
help output. Superstrings such as `--json-output` must not satisfy `--json`.

## Agent Roster Normalization

The normalizer accepts:

- `agents.list` as a list of objects with nonempty unique `id` strings;
- `agents.entries` as an object keyed by nonempty agent IDs, where each value
  is an object and any embedded `id` must match its key;
- an object containing only `agents.defaults`, which represents no explicit
  roster yet.

It rejects:

- non-object `agents` payloads;
- malformed roster values or entries;
- duplicate IDs;
- both roster forms being populated at once;
- an embedded modern-entry ID that disagrees with its key;
- a CLI-listed dedicated agent that is absent from the configured roster;
- a configured dedicated agent that is absent from the CLI list.

The existing `openclaw agents list --json` parser will also accept keyed
`entries` objects and normalize the key into each record's `id`.

## Missing Paths and Errors

Reading the root `agents` path avoids the normal fresh-install missing-child
case. If even the root is absent, the helper may treat it as empty only when
OpenClaw returns an exact missing-path diagnostic for `agents`:

- the documented JSON error on stdout; or
- the same exact plain-text diagnostic on stdout or stderr for compatible
  older releases.

The requested path must match exactly. Permission errors, invalid JSON,
different missing paths, extra ambiguous data, and all other nonzero results
remain fatal before mutation. After `agents add`, the roster read is required
to succeed and expose the new agent.

## Setup Flow

1. Complete version and capability preflight.
2. Validate SecretRef capabilities without mutation when a token is enabled.
3. Read CLI agents and normalized root agent configuration.
4. Reject inconsistent existing state and unsafe approval policy.
5. Return a truthful no-mutation preview for dry-run mode.
6. Provision the gateway token only after read-only validation succeeds.
7. Synchronize shipped skills and create the agent when it is absent.
8. Re-read the root configuration and resolve the actual modern or legacy
   agent prefix.
9. Apply the same skills, tools, sandbox, CRM URL, SecretRef, and allowlist
   settings through that prefix.
10. Perform the existing authoritative readbacks, config validation, skill
    eligibility checks, execution-policy checks, and gateway restart.

## Similar-Issue Audit

The audit covers every help-capability check and every `config get` call in the
helper. Optional-path handling must be explicit rather than added to the
general required-command helper. Sensitive token readbacks remain required and
continue suppressing error output. Post-creation roster reads remain required.

No broad fallback will convert arbitrary command failures into empty values.

## Testing

Regression tests will prove the red and green behavior for:

- commands printed after a nested `Examples:` block;
- a real top-level section ending the command list;
- exact command and option matching;
- a fresh `agents.defaults`-only configuration;
- an entirely missing root `agents` path with exact JSON and legacy text
  diagnostics;
- wrong-path, permission, malformed JSON, and ambiguous errors failing before
  mutation;
- modern keyed entries and legacy list rosters;
- conflicting dual rosters, duplicate IDs, and key/ID mismatches;
- fresh creation followed by modern or legacy roster discovery;
- dry-run behavior without mutation or an invented schema;
- existing-agent idempotence and authoritative tool-policy readback under both
  schemas.

The focused setup suite, complete backend suite, dashboard production build,
and repository diff checks must pass before a pull request is opened.

## Documentation and Live Acceptance

`docs/LOCAL-AI.md` will briefly explain that setup supports current keyed and
legacy list rosters and that a missing explicit roster is normal before the
first dedicated agent is created.

Automated verification does not replace a real OpenClaw run. Chris or another
tester must rerun setup on a clean installation, then start the CRM and run the
documented live agent and CRM doctor checks.

## References

- [OpenClaw config CLI](https://docs.openclaw.ai/cli/config)
- [OpenClaw agent configuration migration](https://github.com/openclaw/openclaw/blob/main/docs/tools/multi-agent-sandbox-tools.md)
