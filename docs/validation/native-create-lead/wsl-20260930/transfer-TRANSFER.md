# Transfer this native create-lead branch to WSL

Final HEAD: `f04a67e559474401a4d2475630d43503b9c0255c`

Required existing base: `186335fba6db921398cb63ce8259fd4fbcf74614`

Bundle SHA-256: `467cba5ff5812cfc717da4c6c93bc2bc9d147c53266ac69747a2ec09d2cc92cf`

The archive contains the same standalone bundle, this message, a manifest, and
bundle/manifest checksums. Extract it and import only the bundle.

Reviewed implementation: `947bb7010c4d23c8ca01180423a747dc7d088fb8`. The final commit contains evidence
and handoff corrections only. Independent code and scoped correction reviews
passed. Local checks: 724 backend, 50 plugin, 20 browser tests and build passed;
five existing Python deprecation warnings remain. Proposal inference was
simulated locally. WSL live acceptance and a fresh dependency install remain
unverified. No test suites were rerun merely for packaging.

Bundle verification, import into a temporary repository containing the required
base, exact final HEAD comparison, and Git connectivity checks all passed.

The eight implementation decisions and their tradeoffs are committed in
`docs/validation/native-create-lead/REVIEW-DECISIONS.md`.

Import and validate this branch on the WSL machine. Keep the local model and native
OpenClaw path. The next gate is live acceptance, before more features or integration.
Do not merge, push, deploy, upgrade the runtime, or modify the original CRM/config.

The transfer includes the `720cf52` read/scope fixes plus capability identity,
runtime human unlock, durable native lead proposals, human edit/approve/deny,
explicit close/recovery, browser coverage and the isolated acceptance helper.
It is an experimental implementation, not yet a live-validated release.

## Import into a separate worktree

Use the bundle and checksum from the accompanying transfer manifest. It requires
this existing commit in the receiving repository:
`186335fba6db921398cb63ce8259fd4fbcf74614`.

Run from the receiving repository, substituting the actual bundle location. The
two existence checks run before fetch so no preexisting destination ref or worktree
is silently advanced or reused:

```bash
bundle_path=/absolute/path/to/openhouse-native-create-lead.bundle
sha256sum "$bundle_path"
git cat-file -e '186335fba6db921398cb63ce8259fd4fbcf74614^{commit}'
git bundle verify "$bundle_path"
if git show-ref --verify --quiet refs/heads/codex/native-create-lead-review; then
  echo 'STOP: destination branch already exists' >&2
  exit 1
fi
if test -e ../openhouse-native-create-lead-review; then
  echo 'STOP: destination worktree path already exists' >&2
  exit 1
fi
git fetch "$bundle_path" refs/heads/codex/native-create-lead:refs/heads/codex/native-create-lead-review
git worktree add ../openhouse-native-create-lead-review codex/native-create-lead-review
cd ../openhouse-native-create-lead-review
git status --short
git rev-parse HEAD
```

Require the manifest's final HEAD and a clean worktree. Stop if the base is absent,
checksum differs, destination branch/worktree already exists, or import fails;
report the exact safe error instead of forcing, resetting or overwriting anything.
The archive and standalone bundle are equivalent; import only one.

## Live gate

Follow `docs/NATIVE-CREATE-LEAD.md` in the imported checkout in order, using the
already installed pinned OpenClaw/Ollama/Qwen runtime and synthetic fixtures.
Build this checkout's dashboard with its matching dependencies. If runtime
versions differ, report the actual versions before treating any result as a
reproduction of the pinned environment.

1. Run the isolated protected read fixture first. Verify ten unfiltered reads,
   37 total / 25 displayed, scope errors for filtered/time/page requests, and
   refusal of writes. Record new evidence for the read fixes.
2. Run the separate empty proposal fixture. Verify the dedicated native agent
   calls only `openhouse_propose_lead` and creates a pending proposal, with zero
   leads before human approval. Edit name/email in the existing dialog, approve,
   and verify exactly one stored lead with the edited values.
3. Deny a new proposal, then verify no added lead. Replay/status/duplicate approval
   must not run inference again or duplicate the lead. Restart the fixture and
   recover pending, approved and denied states. Exercise explicit close if an
   actual request becomes uncertain; never silently retry it with a fresh ID.

Keep human and agent keys separate. The guide shows how to unlock privately.
Never place either key in messages, screenshots, URLs, committed evidence or
gateway logs sent back for review. Do not use the legacy setup helper in
capability mode. Stop only the helper's owned fixture children when finished.

## Return for review

Return the tested commit, exact runtime/model versions and digest, each case's
outcome and request ID, final lead/pending counts, replay/duplicate/restart results,
and sanitized screenshots or transcripts. Label failures and missing evidence;
do not retry until a passing run hides the original failure. Report preservation
and cleanup status. If a code fix is needed, describe the failure first and keep
any work isolated; do not broaden the workflow or enable another agent tool.

Local automated evidence is in `docs/validation/native-create-lead/README.md`. Browser proposal inference
was simulated at the completion boundary while authentication, HTTP tool
submission, SQLite and human approvals were real. Live acceptance of this branch
has not run on the Mac. Historical WSL read evidence remains historical.
