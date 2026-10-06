# Native first-run candidate: Ubuntu 24.04 / WSL2

This is the **installation candidate**, not a certified first-run release. Artifact
hashes and local automated checks are recorded under
[validation/native-first-run](validation/native-first-run/). A fresh WSL install,
GPU/model performance, real interruption and offline operation still require the
[independent acceptance run](validation/native-first-run/ACCEPTANCE.md).

The supported AI scope is unfiltered lead counts/directory reads and proposing a
name with optional email/phone for human review. Two isolated OpenClaw gateways
share one empty, persistent CRM. Neither gateway receives the human credential.
Other AI workflows are not configured. Existing installations use [SETUP.md](SETUP.md);
this guide does not migrate their data or configuration.

## 1. Prepare an independent Linux environment

Use Ubuntu **24.04 x86_64**, Python **3.12**, Git, a supported local GPU/driver and
space for the model, runtimes, build caches and database. GPU and memory minimums
have not yet been measured for this candidate. Record `uname -a`, `lscpu`, `free -h`,
`df -h`, `/etc/os-release`, and your GPU/driver details. On NVIDIA WSL run
`nvidia-smi`; install the Windows host driver through its documented WSL mechanism,
not a Linux display driver in the distro. This guide's Ollama archive is the
Linux amd64 build; an AMD/ROCm installation needs separate artifact verification.

For acceptance, create a **new WSL distro / disposable Ubuntu installation and
normal Linux user**, with its own HOME. Keep the old distro, gateway and files
intact. A new checkout alone is not a fresh installation. This native installer
intentionally refuses macOS, native Windows, other distributions and other Python
versions. Intel macOS can run Ollama on CPU, but is not this candidate's target.

In the new Ubuntu environment:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates xz-utils zstd build-essential python3.12 python3.12-venv
python3.12 --version
umask 077
```

## 2. Get the exact source and pinned prerequisites

Use the full candidate commit from the handoff. Do not substitute `main` or latest.
Choose fresh paths; the following names must not already exist:

```bash
export OHI_COMMIT='REPLACE_WITH_FULL_CANDIDATE_COMMIT'
export OHI_RUNTIME="$HOME/.local/share/openhouse-runtime-candidate"
export OHI_STATE="$HOME/.local/share/openhouse/native"
test ! -e "$OHI_RUNTIME"
test ! -e "$OHI_STATE"
git clone https://github.com/tobywashere/open-house-intelligence-crm.git "$HOME/openhouse-first-run"
cd "$HOME/openhouse-first-run"
git checkout --detach "$OHI_COMMIT"
git status --short
mkdir -m 700 -p "$OHI_RUNTIME/downloads"
```

If using an offline Git bundle, import the handoff branch into a fresh clone with
the specified base already available, verify its SHA-256 and `git bundle verify`,
then check out the exact commit. Do not copy a previous venv or dashboard build.

Download the exact three artifacts and validate every byte against the committed
provenance. This command prints names/status only, never credentials:

```bash
python3.12 - <<'PY'
import base64, hashlib, json, os, pathlib, urllib.request
out = pathlib.Path(os.environ['OHI_RUNTIME']) / 'downloads'
for artifact in json.loads(pathlib.Path('docs/validation/native-first-run/runtime-provenance.json').read_text())['artifacts']:
    target = out / artifact['url'].rsplit('/', 1)[1]
    digest = hashlib.new(artifact['algorithm'])
    with urllib.request.urlopen(artifact['url'], timeout=60) as response, target.open('xb') as stream:
        while block := response.read(1024 * 1024):
            stream.write(block); digest.update(block)
    actual = base64.b64encode(digest.digest()).decode() if artifact['algorithm'] == 'sha512' else digest.hexdigest()
    if actual != artifact['expected']:
        raise SystemExit('Artifact verification failed: ' + artifact['name'])
    print(artifact['name'] + ': verified')
PY
mkdir -m 700 "$OHI_RUNTIME/node" "$OHI_RUNTIME/openclaw" "$OHI_RUNTIME/ollama"
tar -xJf "$OHI_RUNTIME/downloads/node-v24.15.0-linux-x64.tar.xz" --strip-components=1 -C "$OHI_RUNTIME/node"
tar --zstd -xf "$OHI_RUNTIME/downloads/ollama-linux-amd64.tar.zst" -C "$OHI_RUNTIME/ollama"
export PATH="$OHI_RUNTIME/node/bin:$PATH"
node --version
npm install --prefix "$OHI_RUNTIME/openclaw" --omit=dev --no-audit --no-fund "$OHI_RUNTIME/downloads/openclaw-2026.8.1-beta.3.tgz"
"$OHI_RUNTIME/openclaw/node_modules/.bin/openclaw" --version
"$OHI_RUNTIME/ollama/bin/ollama" --version
```

Required versions: Node **24.15.0**, OpenClaw **2026.8.1-beta.3** (recorded build
`5831b80`), Ollama **0.32.15**. OpenClaw's npm dependencies are resolved during this
private install; retain its generated package-lock and `npm ls --all --json`
privately as dependency provenance. The root OpenClaw artifact is verified; this
is not a claim that upstream published a fully locked transitive dependency tree.
No global OpenClaw install or original profile is changed.

## 3. Start your own Ollama and acquire the model

In a dedicated terminal in the new distro, set the same `OHI_RUNTIME` and run:

```bash
umask 077
env -i HOME="$HOME" PATH="/usr/bin:/bin" \
  OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$OHI_RUNTIME/models" \
  "$OHI_RUNTIME/ollama/bin/ollama" serve
```

If port 11434 is occupied, identify the conflict and use the independent distro;
do not terminate an original service. The OpenHouse launcher never owns Ollama.
In the original setup terminal:

```bash
OLLAMA_HOST=127.0.0.1:11434 "$OHI_RUNTIME/ollama/bin/ollama" pull qwen3.5:9b
python3.12 - <<'PY'
import json, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open('http://127.0.0.1:11434/api/tags', timeout=5) as response:
    models = json.load(response)['models']
expected = '6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7'
assert any(m['name'] == 'qwen3.5:9b' and m['digest'] == expected for m in models), 'Pinned model digest unavailable; stop and report'
print('Pinned model digest verified')
PY
```

A changed mutable model tag is a blocker, not permission to substitute another
model. Configuration fixes context at 16384, maxTokens at 2048, thinking off,
localModelLean false and no provider fallback. Observe GPU use and memory during
acceptance; availability alone does not establish acceptable latency.

## 4. Prepare OpenHouse

From the checkout:

```bash
python3.12 scripts/native_local.py setup --state "$OHI_STATE" \
  --node "$OHI_RUNTIME/node/bin/node" \
  --openclaw "$OHI_RUNTIME/openclaw/node_modules/.bin/openclaw"
```

Optional first-setup port flags: `--port 18080 --read-port 18880 --proposal-port 18881`.
All must be distinct, unused, unprivileged loopback ports. They become immutable
installation settings. State paths must be absolute without symlinked ancestors.
Use a checkout under the Linux filesystem, not a symlinked Windows mount.

Setup creates a stable `.venv-native` in this checkout, installs the hashed Python
lock, runs `npm ci` and a production build from clean tracked dashboard inputs,
and creates a private empty SQLite database. Ambient dotenv/Vite values do not
enter that build. State directories are 0700 and keys/config/DB are 0600. Existing
state is verified, never reset. The final manifest binds source, config, build,
ports, locks and runtime. Do not edit/move the prepared checkout afterward.

Setup prints a human-key **file path**, not its contents. Open that file locally
in a private editor and paste the key only into the dashboard unlock form. Never
put it in a command argument, frontend build variable, screenshot or report.

## 5. Start, unlock and exercise the shared CRM

```bash
python3.12 scripts/native_local.py start --state "$OHI_STATE"
```

Leave this terminal open. It owns one backend and two gateways, and reports a
loopback dashboard URL when authenticated checks pass. Readiness does **not**
prove model/tool correctness. Open the URL in a browser and unlock using the
human key. Refreshing or locking requires another unlock; credentials stay in memory.

1. In **CRM reads**, ask `How many leads are there?` Expect a verified zero result.
2. In **Propose lead**, request a synthetic name and optional email/phone. A pending
   proposal must leave the lead count at zero. Edit its name and approve once.
3. Read the CRM again; the same database must show one lead with the edited name.
4. Try a second synthetic proposal and deny it. Check the original lead remains.

Keep unsupported filtering, writes in read mode, and broad CRM actions outside
this AI scope. Scan, voice and legacy lead processing are not configured here.
Daily summary shows a missing state until a separate configured publisher exists.

## 6. Diagnose, stop and restart

In another terminal, from the same checkout:

```bash
python3.12 scripts/native_local.py doctor --state "$OHI_STATE" --json
python3.12 scripts/native_local.py doctor --state "$OHI_STATE" --live-read --json
```

Default doctor validates files, pinned runtimes/model, backend capability identity
and both authenticated gateway health snapshots. It does not start, repair,
migrate or infer. Its pinned probe is
`openclaw --profile PROFILE gateway call health --port PORT --json --timeout 1500`;
the token comes from the private profile. The pinned CLI routes this command
through `callGatewayReadOnlyCli(sharedStateMode='read-only')`. It must report the
expected agent. No token appears in arguments or public diagnostics.

`--live-read` makes exactly one existing native-read request and reports safe
count/timing metadata. It does not test proposal inference. Exit codes: 0 checks
passed; 1 installed but stopped/unready or live read failed; 2 invalid installation
or runtime/CLI configuration. Stopped services do not imply a corrupt database.

Ctrl-C in the start terminal stops only its owned services, including descendants.
A required child failing also stops the other owned services. Your separate Ollama
terminal is untouched. Starting again uses the same database/keys and recovers
uncertain request status without redispatching inference. Check/close an uncertain
proposal; do not submit it again under a new ID to hide the failed attempt.

To repeat setup, omit the immutable executable/port overrides:

```bash
python3.12 scripts/native_local.py setup --state "$OHI_STATE"
```

## Recovery boundaries

- Busy lock: stop the owning setup/start normally; never delete lock files.
- Wrong runtime/model: restore the documented versions/digest. No automatic pull.
- Incomplete setup: inspect the reported private `.STATE.incomplete-*` sibling and
  its setup log. Preserve it; after fixing the cause, use a fresh empty state path.
- Changed source/build/config/key: restore the exact prepared files/revision.
  There is no force/reset/upgrade command. Keep existing DBs; migration is deferred.
- Occupied port: no existing listener is adopted or killed. Select ports only when
  preparing a new installation.
- Child failure: private logs are under `STATE/logs`. They may contain model data;
  do not upload them. Publish only sanitized case outcomes.

One active native installation per checkout is supported because its build and
venv are shared. Use separate fresh checkouts for concurrent installations. Local
file permissions separate OS users; same-user malicious processes are outside that
boundary. All application and model endpoints remain on loopback.
