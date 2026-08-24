# Final Fix D Report

Base: `ca9d536`

## Scope completed

Checkpoint D now aligns the automated acceptance runner, supported-hardware
evidence process, approved design, and beginner setup guides.

- Write-enabled acceptance adds a natural-language `book_appointment` test for
  one suitable existing lead. It creates a unique future time and location,
  snapshots Pending proposals and appointments before chat, requires one exact
  new proposal and the real reported ID, proves no appointment was applied,
  denies only the safely owned proposal, and proves it remains unapplied with
  no owned proposal left Pending.
- The booking path never approves. Missing leads, malformed baselines,
  fabricated IDs, duplicate matching proposals, concurrent unrelated
  proposals, uncertain snapshots, denial failures, and post-write verification
  failures produce honest failures. Cleanup still runs, and ambiguous ownership
  is never guessed.
- Read-only acceptance remains read-only. Proposal attempts still require
  `--allow-test-write`.
- `scripts/capture_setup_evidence.py` runs setup twice as a separate explicit
  action. It saves private sanitized logs, records the tested revision and
  structured outcomes, runs a read-only final-state probe after each setup, and
  compares the full sanitized state fingerprints. Acceptance rejects missing,
  incomplete, failed, malformed, wrong-revision, probe-failed, or mismatched
  evidence and exposes only bounded booleans and counts in its report.
- Discord binding is reported as `Discord delivery (manual hardware)`. An
  unbound agent is `SKIP`; a bound agent is `WARN`, never `PASS`. The automated
  runner does not claim channel delivery from configuration.
- README and Mac mini, WSL, Linux/GB10, local-AI, contract, approved-design, and
  approved-plan documentation now describe the same automated coverage and
  external gates. Generated local evidence files are ignored by default.

## RED evidence

Adversarial tests were observed failing before their corresponding production
changes for:

- missing, one-run, failed, probe-failed, mismatched-state, malformed, and
  wrong-revision setup evidence;
- state changes after a long shared output prefix;
- evidence output conflicts before setup starts;
- booking success, real proposal ownership, fabricated IDs, unrelated
  concurrency, duplicate matching proposals, no leads, unavailable baselines,
  post-write snapshot uncertainty, denial failure, and post-write appointment
  verification failure;
- bound Discord configuration being incorrectly treated as automated delivery
  proof;
- missing beginner documentation and generated-report ignore rules.

## Verification

Fresh verification after the final changes:

- Acceptance, beginner docs, doctor, and daily brief:
  `145 passed`, with 5 pre-existing deprecation warnings.
- OpenClaw setup tests excluding the loopback-only redirect cases:
  `270 passed, 15 deselected`, with the same warnings.
- Loopback redirect security cases, run with temporary local listeners:
  `15 passed, 270 deselected`, with the same warnings.
- CRM chat and Pending approval regressions:
  `116 passed`, with the same warnings.
- Python compilation for the changed runner, helper, and tests: passed.
- `git diff --check`: passed.

No live OpenClaw setup, model, Mac mini, WSL, GB10, Discord, or other hardware
success is claimed by this checkpoint.

## Remaining external evidence

On the exact revision being considered for merge, a supported-hardware tester
must run:

```bash
python3 scripts/capture_setup_evidence.py --output openhouse-setup-evidence.json
bash scripts/serve.sh
python3 scripts/doctor.py --live-agent --live-crm
python3 scripts/acceptance_openclaw.py --json --allow-test-write \
  --setup-evidence openhouse-setup-evidence.json \
  > openhouse-acceptance.json
```

The tester must inspect the sanitized evidence file, both sanitized setup logs,
and the acceptance JSON. Required checks and cleanup must pass, including
`Setup twice`, exact lead count, invalid-write safety, reviewed create-lead,
reviewed booking, briefing truthfulness, proposal denial, and session cleanup.
The runner never approves either proposal.

If Discord is bound and Discord is in scope for the merge, a person must also:

1. ask Discord for the CRM lead count and compare it with the real CRM count;
2. ask Discord for one uniquely marked disposable write;
3. verify the real proposal appears in dashboard Pending approvals and remains
   unapplied;
4. deny that proposal and record the result.

Binding output alone is not delivery evidence. Merge remains gated on this
manual result whenever Discord is in scope.
