# OpenClaw single-action experiment

This experiment keeps OpenClaw and local inference. It tests whether removing the
model's finish call improves the lead-directory experience. It does not replace
the dashboard's current chat flow or configure any agents.

The candidate uses one OpenClaw completion to select one CRM operation, invokes
the existing native CRM tool through the gateway, validates its receipt, and
renders the answer in Python. The current dashboard-channel tool protections
remain intact. This is a smaller change than allowing an OpenClaw agent to own
the entire conversation loop; that larger architecture is not tested here.

## Before running on the WSL test machine

- Use the already configured dedicated CRM agent at the known working OpenClaw
  and local model versions. Existing setup/channel verification must have passed.
  The comparison wrapper cannot independently stop native tools in an incorrectly
  configured gateway; the installed dashboard guard supplies that protection.
- Start the local model, gateway, and existing CRM normally. Do not rerun setup
  with this experiment merely to compare chat.
- Keep the model, database, agent settings, and runtime version constant across
  both paths. Avoid editing leads during the comparison.
- Use an experiment checkout that contains `scripts/compare_crm_chat.py`, with
  the backend dependencies installed. The existing CRM can keep running from its
  existing checkout; this script does not install plugin or skill material.
- Verify `AGENT_ID`, `AGENT_GATEWAY_URL`, `CRM_API_URL`, `AGENT_GATEWAY_TOKEN`, and
  `OHI_API_TOKEN` match the running services. Set tokens in the environment or a
  private `.env`, never in command arguments or reports. No cloud model is selected
  by this script; the existing agent configuration controls inference location.

From the experiment checkout, use Bash (also inside WSL):

```bash
source scripts/load-env.sh
load_repo_env .env
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/compare_crm_chat.py \
  --live-read-only > comparison.json
```

If using a linked worktree with the parent repository's existing virtualenv,
replace `.venv/bin/python` with `../../.venv/bin/python`. Copy only the necessary
settings privately into the experiment environment; the script does not locate
or read another checkout's `.env` automatically.

The default API URL is `http://127.0.0.1:8080/api`. Only loopback URLs are accepted.
The per-path deadline is 30 seconds; `--timeout 120` increases it for a slower local
model. There are six path runs: three directory prompts through each path. The
baseline can make multiple OpenClaw completion requests within each run.
Run both paths with the same deadline. The order alternates, but this small sample
is not a statistically controlled latency or reliability benchmark.

The script prints JSON and exits 0 only when all six count checks pass and the API
count is unchanged at the final snapshot. Exit 1 means failed or unavailable;
exit 2 without `--live-read-only` means no requests were sent. It may take about
three minutes at the default deadline if every request exhausts its budget.

Both paths receive a narrowed read-only operation catalog. The baseline retains
its finish function and existing runtime loop; the candidate has no finish
function. Only `list_lead_directory` and `get_lead_context` can pass the wrapper's
native invocation boundary. All proposals, approvals, and other writes are
rejected there even if the model requests them.

The baseline is a controlled invocation of the existing chat algorithm, not a
browser or `/api/chat` test. It does not store CRM chat history or perform the
dashboard route's knowledge augmentation. Gateway sessions and normal read audit
entries can still be created. Each case has a fresh session; this script does not
delete gateway sessions or modify the installation to clean them up.

## Reading the output

- `model_calls`: application requests to OpenClaw, not internal provider rounds.
- `tool_calls`: permitted native CRM invocations; rejected requests are separate.
- `elapsed_seconds`: time for the complete path, including tool invocation.
- `receipt_ok`: whether the existing receipt validator accepted the tool result.
- `failure_stage: model_selection_or_validation`: no permitted native call occurred.
- `failure_stage: tool_or_receipt`: tool execution or its evidence failed.
- `failure_stage: finish_or_render`: baseline obtained valid evidence but did not
  return the expected directory count (including a different valid read operation).
- `api_count_stable: false`: count comparison is inconclusive; inspect the final
  snapshot error or repeat with an idle database.

The baseline checks its application-rendered directory count. The candidate checks
the normalized directory receipt's total. The candidate does not accept model
prose as evidence. Counts and operation names appear in the report; names, emails,
model prose, credentials, and raw tool payloads do not.

For the exact returned replies, explicitly request a **new private file**:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/compare_crm_chat.py \
  --live-read-only --capture-private-replies /tmp/ohi-private-replies.json \
  > comparison.json
```

The capture is created with owner-only permissions and never overwrites an
existing file. It contains potentially sensitive CRM details, bounded to 8,000
characters per reply, plus the generated session identifiers. It is **not a
sanitized report**. Redact it manually before sharing. Failed model intermediates
are not included; the returned application reply and safe failure stage are.

## Local checks and their limits

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest \
  backend/tests/test_single_action.py \
  backend/tests/test_single_action_lifecycle.py \
  backend/tests/test_compare_crm_chat.py -p no:cacheprovider -q
npm --prefix openclaw-plugins/openhouse-crm test
```

Unit tests script the unavailable model/gateway boundary. Lifecycle tests use a
scripted selection, the real CRM skill dispatcher, FastAPI handlers, and temporary
SQLite databases. They verify pending state, edited approval, repeat refusal,
restart persistence, denial, notes, reminders, and booking conflicts. External
integrations are disabled. This does not prove real model selection, plugin
installation, browser behavior, backup restoration, or provider delivery.

The seven-operation candidate requires enough information for one operation.
It has no multi-turn record lookup or conversational clarification workflow.
Do not use it as a drop-in replacement for unrestricted dashboard chat.

## Decision after hardware results

Keep the original failed WSL reply as a separate diagnostic case; its cause is
unknown until that response and its tool errors are available. Run this pilot
without treating one success as a release pass. If the candidate improves results,
expand the evaluation to paraphrases, missing/ambiguous fields, and a disposable
configured CRM for live proposal and approval tests. Add browser and restore
checks before changing production routing. If it does not improve results, retain
the current implementation and investigate the measured failure stage.

No winner has been established by this experiment's automated tests.
