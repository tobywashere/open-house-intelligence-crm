# WSL native create-lead acceptance — 2026-09-30

The documented live read and proposal gate passed on the transferred code. A restart browser-driver navigation failure was preserved and corrected in the acceptance driver only; no model request was repeated, and no implementation files were changed.

## Transfer and tested revision

- Archive SHA-256: `7c34afd6a42ab86cb0248503777ad8d8252f4b25fa062138ff15a7479fc0957e` (computed locally; no independent outer archive checksum was supplied).
- Bundle SHA-256: `467cba5ff5812cfc717da4c6c93bc2bc9d147c53266ac69747a2ec09d2cc92cf` — matches both TRANSFER.md and manifest.
- Manifest SHA-256: `20ed678d1eaba8d71899c864f37e5c51eb538919cefa8e38bb5666f54bc8e923` — matches SHA256SUMS.
- Required base: `186335fba6db921398cb63ce8259fd4fbcf74614` — present; `git bundle verify` passed.
- Tested final HEAD: `f04a67e559474401a4d2475630d43503b9c0255c`.
- Implementation identified by sender: `947bb7010c4d23c8ca01180423a747dc7d088fb8`.
- Imported branch: `codex/native-create-lead-review` in `/home/ankus/GitHub/openhouse-native-create-lead-review`. Destination branch/path were absent before import; imported worktree was clean.
- No merge, push, deployment, runtime upgrade, or implementation correction was performed. This evidence remains uncommitted for review.

## Live acceptance results

| Case | Result |
| --- | --- |
| Ten fixed unfiltered dashboard requests | 10/10 HTTP 200; 37 total / 25 displayed; matching fresh receipt ID for every request; median 4612 ms |
| Closed/today/Seattle/second-page scope requests | 4/4 HTTP 400 `unsupported_request`; visible scope error and no verified result |
| Delete all leads | HTTP 400 `unsupported_request`; no verified result and no record changes |
| First native proposal | Pending `create_lead` for Synthetic WSL Person, synthetic@example.invalid, 555-0100; zero leads before approval |
| Human edit and approve | Name changed to Human Edited WSL Person and email to edited@example.invalid; exactly one lead, stored result ID 1 matches panel |
| Refresh/unlock recovery | Same approved ID/result; zero new proposal POSTs |
| Second native proposal and denial | Denied, lead count stays one |
| Replay/status/duplicate approval | Replay HTTP 200 with identical stored result; duplicate approval HTTP 400; no new lead or inference |
| Restart pending recovery | Third proposal remained pending with identical payload; whole fixture content hash unchanged across restart |
| Deny recovered pending proposal | Denied; earlier approved and denied states survive; one lead remains |

Public proposal request IDs:

- Approved: `69bb21fe6aab40e08fd69e84c2718817`
- Denied: `49d506d364c449a4aafb3bc57feb50a9`
- Pending across restart, subsequently denied: `4771e02421a94cec8e70d93de66589f0`

All read request IDs, prompts, latencies, response bodies, displayed results and scope errors are in `read-live-20260930/results.json`. Proposal outcomes and their stored results are in `proposal-live-20260930/initial.json` and `restart-continuation.json`.

`native-traces.json` was extracted from only the isolated agent trace databases. Each of the ten read sessions exposed/called only `openhouse_crm`; each of the three proposal sessions exposed/called only `openhouse_propose_lead`. Every session had one native tool and zero client tools, used local Ollama/Qwen, and took two internal inference rounds. Read handler results match the displayed receipts. Proposal tool results are durable pending records, not generated prose.

The three proposal sessions produced six assistant inference messages and three native tool calls. The transcript hash is identical before replay, after replay, across restart, and after the final denial/status checks. See `probe-before-restart.json`, `probe-after-restart.json`, and `probe-final.json`.

## Preserved failure and limits

The first restart browser-driver attempt tried to click Propose lead after unlock. The application had already recovered and automatically opened Pending approvals, which correctly blocked the click. The 15-second driver timeout is retained in `proposal-live-20260930/restart.json`; it was not an inference or application request failure. The continuation followed the already-open dialog, verified the existing pending ID/payload, and denied it. It created no fresh request ID and ran no additional inference.

No actual proposal became unknown/uncertain. The guide's conditional explicit-close path was therefore not exercised live. No timeout or late-result race is claimed as validated by this run. The sender's simulated close tests remain separate prior evidence.

The immediate post-approval screenshot still shows cached dashboard summary counts at zero while the verified Approved lead panel shows ID 1. API/database checks confirm exactly one stored lead; refresh/restart updates the broader dashboard. This transient summary view was not used as creation evidence.

## Runtime and method

OpenClaw `2026.8.1-beta.3 (5831b80)`; Ollama client/server `0.32.15`; model `ollama/qwen3.5:9b`, Q4_K_M, 9.7B; digest `6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`. Context 16384, maxTokens 2048, thinking off, localModelLean false, local Ollama only, empty fallbacks. Node v24.15.0, Python 3.14.4, Playwright 1.62.1, Microsoft Edge 154.0.4258.37. Details are in `runtime.json`.

The checks used visible Windows Edge driven by Playwright against the actual WSL backend/gateway/model. No HTTP routing mocks, simulated completion seam, or tool substitutions were used. The small acceptance drivers automate the documented human UI sequence, privately read each fixture's human.key, and unlock through API token. Keys are not logged or saved in browser storage/evidence. Daily-summary overlays were suppressed with the same local-storage setup as the repository's browser tests. This is automated execution of live acceptance, distinct from the sender's simulated automated suites.

Fresh `npm ci --no-audit --no-fund` installed 185 locked packages in this checkout; `npm run build` passed (TypeScript + Vite 5.4.21, 323 transformed modules). The existing Python environment was reused. No OpenClaw/Ollama/model upgrade occurred. The prior Mac 724 backend / 50 plugin / 20 browser results are sender-reported evidence, not rerun or relabeled as WSL results.

## Reproduction commands used

In the imported WSL checkout, with the existing Node/OpenClaw tools on PATH and the existing project virtual-environment Python:

```bash
cd dashboard
npm ci --no-audit --no-fund
npm run build
cd ..
python scripts/native_proposal_acceptance.py prepare --kind read --profile ohi-native-read-review --port 18083 --gateway-port 18883
python scripts/native_proposal_acceptance.py run --profile ohi-native-read-review --seconds 1800
# Stop the owned read runner, then:
python scripts/native_proposal_acceptance.py prepare --profile ohi-native-proposal-review --port 18082 --gateway-port 18882
python scripts/native_proposal_acceptance.py run --profile ohi-native-proposal-review --seconds 1800
# Restart durability used the same run command, without prepare.
python scripts/native_proposal_acceptance.py snapshot --profile ohi-native-proposal-review
```

Here `python` was `/home/ankus/GitHub/open-house-intelligence-crm/.venv/bin/python`. The runtime PATH was `/home/ankus/.openclaw/tools/node-v24.15.0/bin:/home/ankus/.local/bin:/usr/local/bin:/usr/bin:/bin`. Ollama was initially stopped and was started with the existing `ollama serve`. An initial attempt at `/usr/local/bin/ollama` failed because that path was absent; the installed executable was `/home/ankus/.local/bin/ollama`. No acceptance request was sent before Ollama was ready.

The included `reproduction/` drivers are local acceptance helpers, not product changes. `live-read.cjs` adapts the old tokenless live script for private runtime unlock and the five rejection cases. `live-proposal.cjs initial` runs the first three native proposals; after a fixture restart, `restart-continuation` handles the already-open approval dialog. `probe.py` performs the documented authenticated replay/duplicate checks and verifies inference transcript hashes. Do not run prepare over retained profiles or overwrite this evidence. Use fresh names/output directories for an independent run.

## Final state, preservation and cleanup

- Read fixture: 37 leads, zero pending changes/requests; content hash `eb79615d0537bdf3cfc44493c2c6b5a2a65e92ac23b321a6566e52e8b1a4fe68` unchanged from preparation.
- Proposal fixture: one lead, one event, three proposal/request records: one approved, two denied, zero pending approvals. Final content hash `1a49ed995b517e1e46acebc2d3fc1eebdebed1a29d35aa2af2331176d65e873d`.
- Proposal content hash before and immediately after restart: `7a05cc77b882ab683368b0204e7bd32c1678162e01330ddef5c1ef29aa184b0d`.
- All 24 original CRM/configuration/backup/skill file hashes match before and after. Preservation manifests contain paths/hashes only.
- Both acceptance runners and their owned backend/gateway children stopped gracefully. The task-started Ollama server stopped. Fixture ports 18082/18083/18882/18883 and Ollama 11434 are closed; original gateway 18789 remains active.
- Private fixture keys, databases, and raw gateway/session logs remain outside Git under `~/.ohi-native-acceptance/`. Sanitized evidence contains synthetic records only. Existing Windows changes are preserved and excluded from the evidence archive.

The receiving implementation branch remains at the exact imported HEAD, with only the new uncommitted evidence directory added.
