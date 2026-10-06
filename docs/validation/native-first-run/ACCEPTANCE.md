# Independent first-run acceptance (not yet run)

Use the full commit supplied in the handoff. Follow [the installation guide](../../NATIVE-FIRST-RUN.md)
in a **fresh Ubuntu 24.04 WSL distro or disposable Linux environment**, not the old
acceptance worktree. No earlier pass is inherited. Preserve every failed case;
additional requests are separately numbered cases, never hidden retries.

## Evidence and isolation

Copy `evidence-template.json` into a new private evidence directory. Record the
source commit and clean Git status; OS, kernel, CPU/RAM/GPU and host driver;
Python/Node/OpenClaw/Ollama versions; artifact URLs/hashes; model digest; private
install prefix and fresh HOME/venv/npm/model provenance. Record the commands and
exit codes from prerequisite acquisition through setup. Sanitize before sharing.
Do not capture keys, raw prompts/replies, complete environment dumps or private
logs. Use synthetic contact details only.

Before testing, hash the same original CRM/config files used in the earlier
preservation audit, and record original service ownership/status without changing
it. Keep those original files in the old distro. GPU host drivers may be shared;
record that exception. Hash the original files again after cleanup.

For each case retain ID/time, expected/observed result, one dispatched model
session's count, attempted/successful tool calls, latency, DB counts and outcome.
A successful doctor is readiness; only the explicit live cases certify inference.
Automated browser completion stubs do not count toward these results.

## Required cases

1. Follow the guide without extra undocumented fixes. Initial DB: zero leads and
   zero proposals. Run default doctor and prove no inference or DB/key/config
   changes. Run one live read and verify zero.
2. One real proposal, edited human approval, exactly one stored lead. Before the
   approval there must be zero leads. Read that same installation and see the
   edited record. Retain timings; do not reset to a separate read fixture.
3. Four unsupported read scopes (filter, date, pagination, negation) and one write
   request are rejected without inference. A second valid proposal is denied.
   Repeat the previous approval and prove no additional lead is created.
4. Stop/start the candidate only. Records, key hashes and terminal/pending status
   persist. Repeat setup without overrides and show it preserves DB/config/key
   hashes. No automatic inference on restart, reload, status or repeat setup.
5. Execute the declared delayed-submission case below. This is a real model/tool
   request with a held HTTP transport, not a fabricated response.
6. Disposable copies/cases: remove the model, change a config, occupy a configured
   port with a test listener, and terminate one owned child. Record clear failures,
   unchanged conflicting listener ownership and no duplicate inference. Restore
   exact files/model before continuing. Never delete real state or kill by port.
7. Execute actual reads and proposals with external networking disabled by the
   isolated namespace below. Absence of cloud keys alone is insufficient.
8. Stop all candidate-owned app/relay/Ollama processes. Keep original services
   untouched. Final lead/proposal/request counts must equal the recorded decisions;
   original hashes remain unchanged. Record untested or failed gates explicitly.

## Delayed tool submission: disposable installation only

The relay is a separate acceptance tool. It holds **one** authenticated agent
POST, for one exact request ID, up to 180 seconds from capture. It may outlive the
plugin's 10-second HTTP timeout. That timeout must be retained as an observed
failure. On release it forwards the captured request once; it never regenerates
inference. Do not use this procedure on real CRM data.

Because production startup rejects config drift, the test must explicitly save
and restore **both** its proposal profile and manifest. The following test-only
manifest regeneration declares the altered fixture; it is not a production repair
or upgrade mechanism. Keep the backup outside the install and never publish it.
The code below assumes guide-default ports; adapt all endpoints consistently if
you prepared different ports, and record that variation.

Stop the candidate app normally, leaving only its Ollama running. From the exact
prepared checkout set the existing `OHI_STATE`, then:

```bash
export OHI_CASE_ID="$(python3.12 -c 'import secrets; print(secrets.token_hex(16))')"
export OHI_FAULT="$HOME/.local/share/openhouse-fault-$OHI_CASE_ID"
mkdir -m 700 "$OHI_FAULT"
PYTHONPATH=scripts python3.12 - <<'PY'
import json, os
from pathlib import Path
from ohi_native.runtime import InstallOptions
from ohi_native.state import atomic_json, make_manifest, read_private, write_private
state=Path(os.environ['OHI_STATE']); backup=Path(os.environ['OHI_FAULT'])
profile=state/'proposal/home/.openclaw-ohi-native-proposals/openclaw.json'
manifest=json.loads(read_private(state/'manifest.json'))
write_private(backup/'manifest.original',read_private(state/'manifest.json'))
write_private(backup/'profile.original',read_private(profile))
config=json.loads(read_private(profile))
config['plugins']['entries']['openhouse-proposals']['config']['crmApiUrl']='http://127.0.0.1:18882/api'
atomic_json(profile,config)
o=InstallOptions(Path(manifest['source']['root']),state,Path(manifest['openclaw']),Path(manifest['node']),tuple(manifest['ports']))
atomic_json(state/'manifest.json',make_manifest(o,manifest['runtime']))
print('Disposable relay profile prepared; original profile and manifest retained privately')
PY
python3.12 scripts/native_first_run_relay.py \
  --target http://127.0.0.1:18080/api/agent/lead-proposals \
  --key-file "$OHI_STATE/agent.key" --control "$OHI_FAULT/control" \
  --request-id "$OHI_CASE_ID" --port 18882
```

Leave the relay terminal open. Start the candidate with the normal launcher in a
second terminal. In a third terminal, with the same nonsecret variables, dispatch
exactly one actual native request (not a stub):

```bash
python3.12 - <<'PY'
import json, os, urllib.request
from pathlib import Path
state=Path(os.environ['OHI_STATE']); rid=os.environ['OHI_CASE_ID']
request=urllib.request.Request('http://127.0.0.1:18080/api/chat/lead-proposal',
 data=json.dumps({'request_id':rid,'message':'Propose Relay Synthetic, relay@example.invalid'}).encode(),
 headers={'Content-Type':'application/json','X-API-Token':(state/'human.key').read_text().strip()})
try:
 with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=100) as response:
  value=json.load(response); print(json.dumps({'request_id':rid,'state':value.get('state')}))
except Exception:
 print(json.dumps({'request_id':rid,'transport':'interrupted; check same ID, do not redispatch'}))
PY
```

As soon as `OHI_FAULT/control/captured.json` exists, record its safe timestamp and
ID. Stop the **candidate launcher** with Ctrl-C before releasing the relay. Keep
the relay running. Restart that same launcher, still within the relay's 180-second
window. Then check/close the same request and release once:

```bash
python3.12 - <<'PY'
import json, os, urllib.request
from pathlib import Path
state=Path(os.environ['OHI_STATE']); rid=os.environ['OHI_CASE_ID']
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
headers={'X-API-Token':(state/'human.key').read_text().strip()}
url='http://127.0.0.1:18080/api/chat/lead-proposal/'+rid
with opener.open(urllib.request.Request(url,headers=headers),timeout=5) as response:
 value=json.load(response)
assert value['state']=='unknown', 'Different outcome: retain it; do not claim unknown recovery'
with opener.open(urllib.request.Request(url+'/close',data=b'',headers=headers),timeout=5) as response:
 value=json.load(response)
assert value['state']=='failed' and value['proposal'] is None
(Path(os.environ['OHI_FAULT'])/'control/release').touch(mode=0o600,exist_ok=False)
print('Same request closed; captured call released once')
PY
```

Require relay `result.json` to show `forwarded: true`, `status: 409`; no new pending
proposal or lead; same ID remains closed after another restart. Check the original
ID once more, with no new POST/inference. The existing automated lifecycle tests
cover replay and proposal-wins-close; preserve those as separate automated evidence.
If a proposal was stored before closure, report **proposal won**, not the intended
unknown/late-result pass. If there is no capture, model selection failed; preserve
that failure and stop this case. If 180 seconds expired, report timeout without a
forwarded-call claim. Do not repeat until you get a desired outcome.

Stop the candidate and relay, then restore exact original bytes:

```bash
PYTHONPATH=scripts python3.12 - <<'PY'
import os
from pathlib import Path
from ohi_native.state import read_private
state=Path(os.environ['OHI_STATE']); backup=Path(os.environ['OHI_FAULT'])
for source,target in [('profile.original','proposal/home/.openclaw-ohi-native-proposals/openclaw.json'),('manifest.original','manifest.json')]:
 path=state/target;path.write_text(read_private(backup/source));path.chmod(0o600)
print('Original candidate profile/manifest restored; verify hashes against backup')
PY
```

Verify restored hashes before ordinary startup. Preserve relay metadata and the
backup privately. Publish only sanitized capture/result metadata, case outcomes
and nonsecret artifact hashes.

## Offline acceptance without changing the host firewall

Stop only the candidate app and its candidate Ollama first. Use a new network
namespace in this disposable distro; never disable the host firewall/network.
An administrator creates the namespace, brings up loopback and starts a shell as
**the same unprivileged candidate owner**. Example (replace the user name):

```bash
sudo unshare --net bash -c 'ip link set lo up; ip addr show; ip route show; exec runuser -u CANDIDATE_USER -- bash'
```

Inside that shell, record the namespace inode (`readlink /proc/self/ns/net`),
interfaces and routes. There must be only loopback and no external route. Confirm
an external connection fails with a bounded timeout. Keep GPU devices available
(the namespace changes networking, not devices). Restore the guide's nonsecret
path variables; start the already-installed candidate Ollama with its existing
model directory, then the normal candidate launcher. No downloads are permitted.

Run default doctor, exactly one explicit `--live-read`, and one real proposal plus
human approval using the backend HTTP API **inside the namespace**. Use a browser
started inside the namespace if GUI access is available; a host browser cannot
reach its isolated loopback. API scripts must read the human key from its private
file, not a shell argument. Record that this case tests offline inference/API;
regular browser behavior is covered by the earlier GUI cases. Query the stored
result and final counts. Retain proof that the model ran locally. Stop owned
processes before leaving the namespace shell; no system daemon is installed.

## Return for review

Return a sanitized archive with the completed evidence template, concise report,
exact commands/exit codes, hardware/runtime/model identities, case outcomes and
artifact SHA-256s. State all deviations, failed attempts, untested gates and final
service/preservation status. Do not merge/release, change original services, or
copy old acceptance evidence as a new result. Submit fixes separately with the
specific failing command and a minimal reproducible change.
