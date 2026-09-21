# Native lead proposals and recovery

Use **Propose lead** in the dashboard chat rail to request a name and optional
email/phone through the dedicated native agent. The existing **Pending approvals**
dialog shows the queued fields. Edit them and approve to create a lead, or deny
to create none. The panel shows a lead only from the stored approved result.
**CRM reads** and **General chat** remain separate modes.

Capability mode uses a human key for the dashboard and a distinct agent key for
the gateway. Unlock at runtime; refresh clears the in-memory credential. Only the
current request ID is saved in session storage, never the credential or prompt.
Unlock after refresh, select **Propose lead**, and status is fetched without a
new model call. Decisions in the existing dialog promptly refresh the panel.

Use **Check status** after an uncertain result. It does not run inference.
**Close request** deliberately prevents any late proposal for an unresolved ID.
If a proposal arrived first, it remains available for review. A lost close response
keeps the ID until status confirms the outcome. **New proposal request** appears
only after a confirmed failed, approved, or denied result. Explicit preflight
configuration, validation, or authentication failures permit a fresh send because
the server did not start the request. A network failure or 404 does not prove that.

The [protocol and transport contract](native-proposals.md) documents all routes,
validation, replay, close, credentials and runtime settings. The normal legacy
`setup_openclaw.py` workflow provisions a broad general agent and refuses capability
mode before any CLI/file operation. Do not use it to configure or recover this
native profile, and never give the gateway the human CRM key.

## Automated local reproduction

Install repository dependencies and cached Chromium in the usual development
setup. Build once, then run these checks from the repository root:

```bash
cd dashboard
npm ci
npm run build
npx playwright install chromium
cd ..
python3 -m pytest backend/tests/ -q
node --test openclaw-plugins/openhouse-read/test.mjs openclaw-plugins/openhouse-proposals/test.mjs
python3 scripts/test_native_read_browser.py --suite read
python3 scripts/test_native_read_browser.py --suite auth
python3 scripts/test_native_read_browser.py --suite proposals
```

Use your existing project virtual-environment Python. `PLAYWRIGHT_MODULE` may point
to an already installed Playwright module. Each browser runner creates a temporary
CRM on its own prebound loopback socket, uses explicit child environment allowlists,
and removes the fixture after bounded readiness (20 seconds), tests (180 seconds),
and teardown (5 seconds). The proposal fixture generates distinct disposable keys;
only the human key reaches the browser child. No ambient service credentials or
integration keys are forwarded. Optional `--screenshots /tmp/ohi-proposal-shots`
records synthetic proposal/review/approved screenshots.

**Simulation boundary:** proposal browser tests replace only the async completion
seam with fixed synthetic fields submitted through the actual authenticated agent
HTTP endpoint. Reservation, capability checks, database state, edits, decisions,
replay and close use the real application. The auth browser suite substitutes
responses to exercise precise 401 ordering. Read browser tests include simulated
results and real write/scope rejection. These checks do not certify OpenClaw,
Ollama or model inference. There is no production mock-provider setting.

## WSL live acceptance — pending

Run the following in the existing WSL checkout with the already installed runtime.
The pinned environment is OpenClaw **2026.8.1-beta.3 (`5831b80`)**, Ollama **0.32.15**,
**qwen3.5:9b Q4_K_M**, digest
`6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7`.
Context 16384, maxTokens 2048, thinking off, `localModelLean: false`, local Ollama
only and no model fallback. This reuses the [read acceptance runtime](NATIVE-READ.md).
Do not install/upgrade tools or touch the original gateway, config, profile or DB.
Ollama must already serve that model at `127.0.0.1:11434`; this helper does not start
or stop Ollama.

Historical c05403d read captures remain historical. New **720cf52 read/scope-fix**
acceptance and **native proposal** acceptance are both pending. Never overwrite or
relabel those old captures as evidence for this branch.

### 1. Verify reads and unsupported scope first

Build this checkout's dashboard, then create a fresh private read fixture:

```bash
python3 scripts/native_proposal_acceptance.py prepare --kind read --profile ohi-native-read-review --port 18083 --gateway-port 18883
python3 scripts/native_proposal_acceptance.py snapshot --profile ohi-native-read-review
python3 scripts/native_proposal_acceptance.py run --profile ohi-native-read-review --seconds 1800
```

The runner stays in this terminal. It validates the private configuration, refuses
occupied ports, starts only its own foreground children, waits at most 30 seconds
for readiness, and stops those children on Ctrl-C or at the time bound. Do not
start a second runner for the same profile. Open **http://127.0.0.1:18083** visibly.
Read `~/.ohi-native-acceptance/ohi-native-read-review/human.key` privately in a local
editor and paste it into **API token**, then unlock. Never capture that key in
screenshots, shell tracing, URLs, committed artifacts, or messages.

Select **CRM reads**. Run the ten fixed unfiltered prompts listed in
[`native-read.live.cjs`](../dashboard/tests/native-read.live.cjs) manually through
the visible dashboard. Each should show **37 leads total**, **25 displayed** and
a fresh verified request ID. That older script assumes a tokenless page; do not
run it unchanged against this protected fixture.

Then verify each request returns a scope error with no verified result:

- “How many closed leads?”
- “How many leads were added today?”
- “Show leads in Seattle”
- “Show the second page of the lead directory”

“Delete all leads” must also fail without changing any records. Capture the visible
errors and sanitized response status/code. After these checks, stop the runner
with Ctrl-C and take another snapshot. CRM rows and pending proposals must be
unchanged (dashboard caches are outside the snapshot). Save new screenshots,
request IDs, exact commit, runtime/model versions, results, dates and failures in
a new evidence directory. Do not proceed to proposal acceptance if these fail.

### 2. Native proposal, edit, approve and deny

Create a different private profile and empty synthetic database:

```bash
python3 scripts/native_proposal_acceptance.py prepare --profile ohi-native-proposal-review --port 18082 --gateway-port 18882
python3 scripts/native_proposal_acceptance.py snapshot --profile ohi-native-proposal-review
python3 scripts/native_proposal_acceptance.py run --profile ohi-native-proposal-review --seconds 1800
```

Open **http://127.0.0.1:18082**. Unlock with the separate fixture's private
`human.key`, select **Propose lead**, and send exactly:

> Propose Synthetic WSL Person, synthetic@example.invalid, 555-0100

Record the request ID shown in the panel. Require a real pending `create_lead`
record containing the proposed fields and **zero leads before approval**. The
private gateway logs must show the dedicated `native-proposals` agent using only
`openhouse_propose_lead`; completion prose is not creation evidence. Do not share
raw runtime logs or session files; extract only sanitized tool identity, outcomes
and request IDs.

In **Pending approvals**, change Name to **Human Edited WSL Person** and Email to
**edited@example.invalid**, then approve. Require one lead with those exact edited
values and a stored result ID matching the panel. Refresh, unlock again, select
**Propose lead**, and verify the same approved record without another completion.

Use **New proposal request** for **Synthetic WSL Denied Person**. Deny it in the
same approval dialog. Confirm denied state and that the lead count stays one.

### 3. Replay, duplicate approval, close and restart

Use the following local Python probe after approval. Substitute only the public
request ID copied from the first proposal. It reads the human key privately,
keeps it out of command arguments/output, and talks only to the fixture API:

```python
from pathlib import Path
import httpx

state = Path.home() / '.ohi-native-acceptance/ohi-native-proposal-review'
request_id = 'PASTE_FIRST_REQUEST_ID_HERE'
message = 'Propose Synthetic WSL Person, synthetic@example.invalid, 555-0100'
with httpx.Client(base_url='http://127.0.0.1:18082/api', trust_env=False,
                  follow_redirects=False, timeout=70,
                  headers={'X-API-Token': (state / 'human.key').read_text().strip()}) as client:
    original = client.get('/chat/lead-proposal/' + request_id)
    original.raise_for_status()
    record = original.json()
    assert record['state'] == 'approved'
    replay = client.post('/chat/lead-proposal', json={'request_id': request_id, 'message': message})
    assert replay.status_code == 200 and replay.json() == record
    pending_id = record['proposal']['id']
    duplicate = client.post(f'/pending-changes/{pending_id}/approve', json={})
    assert duplicate.status_code == 400
    assert len(client.get('/leads').json()) == 1
    print({'state': record['state'], 'lead_id': record['proposal']['result']['id'],
           'replay_status': replay.status_code, 'duplicate_approval_status': duplicate.status_code})
```

Require no new gateway completion/tool run for replay or status. Repeat status for
the denied ID and confirm there is still one lead. If an actual request becomes
unknown, use **Check status**, then **Close request** to retire it deliberately.
Only a confirmed failed/no-proposal result may enable **New proposal request**;
a proposal winning the race must still be reviewed. Record any timeout/failure as
such, without retrying inference under the uncertain ID.

For restart durability, leave another proposal pending, note its ID and count,
stop the runner with Ctrl-C, then rerun the same `run` command (do not `prepare`
again). Refresh/unlock, verify the pending row is unchanged, then deny it. Repeat
the probe for the earlier approved and denied IDs; the applied result and count
must survive. Use the snapshot command after stopping to record final counts.

### Isolation and evidence

The helper stores fixtures beneath `~/.ohi-native-acceptance/<profile>` with private
permissions. Each gateway gets a separate private HOME and workspace, so neither
its profile nor cwd dotenv discovery points at original user/repository settings.
Only the generated agent CRM key goes to the gateway; its gateway secret is in
its private profile. The human key and database stay outside the gateway HOME.
The backend gets both CRM keys and its own fixture DB. The parent shell's HOME and
configuration are not changed. The read and proposal agents have separate profiles,
DBs and one-tool allowlists. Preparing an existing profile is refused. Runtime
children are bounded and owned by the foreground runner; no machine service or
original process is stopped.

The native paths still use live gateway inference despite `AGENT_MODE=mock` for
the unrelated general-agent path; integrations and their poller are off. No real
contacts, integrations, cloud providers or original data are used. Private fixture
keys, databases and runtime logs remain outside the repository. Keep only sanitized
new evidence under a distinct validation directory after completing WSL acceptance.
See [local validation provenance](validation/native-create-lead/README.md) for the
checks actually run on the Mac and their limits.
