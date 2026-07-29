# OpenClaw and Runtime Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make startup, OpenClaw readiness, and optional integration status accurate and safe on GB10 and Apple-silicon Mac mini hosts.

**Architecture:** Launchers source one shared environment helper. OpenClaw exposes a structured probe distinct from last successful chat. Integration execution classifies read-only versus non-idempotent tools and never generically replays external writes.

**Tech Stack:** Bash, Python 3.13, FastAPI, httpx, pytest, React, TypeScript.

## Global Constraints

- Loopback remains the default bind address.
- A 401, 403, or 404 is never “agent live.”
- Health polling must not repeatedly invoke the model.
- External send/create actions receive at most one attempt without an idempotency key.
- Status copy distinguishes configured from verified.

---

### Task 1: Consistent `.env` loading

**Files:**
- Create: `scripts/load-env.sh`
- Modify: `scripts/dev.sh`
- Modify: `scripts/serve.sh`
- Create: `backend/tests/test_launchers.py`

**Interfaces:**
- Produces: `load_repo_env [path]`, sourced by both launchers before defaults.

- [ ] **Step 1: Write a failing behavior test**

```python
def test_load_env_exports_values_without_overwriting_explicit_environment(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=9123\nAGENT_MODE=openclaw\n")
    script = (
        "export AGENT_MODE=mock; "
        "source scripts/load-env.sh; "
        f"load_repo_env {shlex.quote(str(env_file))}; "
        "printf '%s|%s' \"$PORT\" \"$AGENT_MODE\""
    )
    result = subprocess.run(["bash", "-c", script], cwd=REPO, text=True, capture_output=True)
    assert result.returncode == 0
    assert result.stdout == "9123|mock"
```

- [ ] **Step 2: Run and verify failure because the helper does not exist**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_launchers.py -q
```

- [ ] **Step 3: Implement and use the helper**

```bash
load_repo_env() {
  local env_file="${1:-.env}"
  [ -f "$env_file" ] || return 0
  while IFS='=' read -r key value; do
    case "$key" in ''|'#'*) continue ;; esac
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [ -n "${!key+x}" ] || export "$key=$value"
  done < "$env_file"
}
```

Both launchers source the helper immediately after changing to the repository
root, call `load_repo_env .env`, then apply shell defaults.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_launchers.py -q
git add scripts/load-env.sh scripts/dev.sh scripts/serve.sh backend/tests/test_launchers.py
git commit -m "fix: load repository environment consistently"
```

### Task 2: Structured OpenClaw readiness

**Files:**
- Create: `backend/app/agent/status.py`
- Modify: `backend/app/agent/base.py`
- Modify: `backend/app/agent/openclaw.py`
- Modify: `backend/app/routers/misc.py`
- Create: `backend/tests/test_openclaw.py`

**Interfaces:**
- Produces: `AgentProbe(status, gateway_reachable, endpoint_enabled, last_chat_ok, detail)`.
- Produces: `OpenClawDriver.probe() -> AgentProbe`.
- Adds: `POST /api/health/agent-check` for an explicit harmless live completion.

- [ ] **Step 1: Write failing probe tests**

Use `httpx.MockTransport` to exercise the real driver boundary:

```python
@pytest.mark.parametrize("code,status", [
    (401, "unauthorized"),
    (403, "unauthorized"),
    (404, "endpoint_disabled"),
    (405, "endpoint_enabled"),
])
def test_probe_classifies_chat_endpoint(monkeypatch, code, status):
    driver = OpenClawDriver(client_factory=client_factory_returning(code))
    probe = asyncio.run(driver.probe())
    assert probe.status == status
```

Add a test that a malformed successful response makes `chat()` return an
actionable failure and records `last_chat_ok=False`, while a valid completion
records `last_chat_ok=True`.

Add a separate regression test for `MockDriver.chat("Which active buyers need a
follow-up?", "dashboard")`; it must return the neglected-lead response rather
than the generic canned answer. Normalize `follow-up` and `follow up` in the
minimal mock implementation.

- [ ] **Step 2: Run and verify missing constructor/probe failures**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_openclaw.py -q
```

- [ ] **Step 3: Implement status storage and dependency injection**

```python
@dataclass(frozen=True)
class AgentProbe:
    status: Literal["mock", "unreachable", "unauthorized", "endpoint_disabled",
                    "endpoint_enabled", "verified", "failed"]
    gateway_reachable: bool
    endpoint_enabled: bool
    last_chat_ok: bool | None
    detail: str | None = None
```

`probe()` sends `OPTIONS` to the configured chat URL and classifies
401/403/404/405/2xx. `_send()` validates the complete response shape and updates
the process-local last-chat state. `connected()` remains compatibility-only and
returns true for `endpoint_enabled` or `verified`, never for 401/403/404.

- [ ] **Step 4: Add explicit live check**

`POST /api/health/agent-check` calls `_send` with:

```text
Reply with exactly READY. Do not use tools and do not change any data.
```

It returns the structured probe afterward. Routine `GET /api/health` only calls
the lightweight probe.

- [ ] **Step 5: Run focused/full tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_openclaw.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
git add backend/app/agent/status.py backend/app/agent/base.py backend/app/agent/openclaw.py backend/app/agent/mock.py backend/app/routers/misc.py backend/tests/test_openclaw.py
git commit -m "fix: report real OpenClaw chat readiness"
```

### Task 3: Prevent duplicate external writes

**Files:**
- Modify: `backend/app/integrations/composio_client.py`
- Modify: `backend/tests/test_composio_client.py`

**Interfaces:**
- Produces: `READ_ONLY_SLUGS` and `max_attempts(slug: str) -> int`.

- [ ] **Step 1: Replace the misleading retry test with behavior tests**

```python
def test_send_email_is_not_retried_after_ambiguous_failure(monkeypatch):
    attempts = install_failing_http(monkeypatch)
    with pytest.raises(cc.IntegrationError):
        cc.execute("GMAIL_SEND_EMAIL", {})
    assert attempts == [1]


def test_fetch_email_retries_once_on_transient_failure(monkeypatch):
    attempts = install_failure_then_success(monkeypatch)
    assert cc.execute("GMAIL_FETCH_EMAILS", {}) == {"messages": []}
    assert attempts == [1, 2]
```

- [ ] **Step 2: Run and confirm send currently attempts twice**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_composio_client.py -q
```

- [ ] **Step 3: Implement per-operation attempts**

```python
READ_ONLY_SLUGS = frozenset({
    "GMAIL_FETCH_EMAILS",
    "GOOGLECALENDAR_FREE_BUSY_QUERY",
})


def max_attempts(slug: str) -> int:
    return 2 if slug in READ_ONLY_SLUGS else 1
```

Loop over `range(max_attempts(slug))`. Calendar event creation, draft creation,
and email send receive one attempt.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_composio_client.py -q
git add backend/app/integrations/composio_client.py backend/tests/test_composio_client.py
git commit -m "fix: do not replay non-idempotent integrations"
```

### Task 4: Accurate status API, dashboard labels, and doctor

**Files:**
- Create: `scripts/doctor.py`
- Modify: `backend/app/integrations/composio_client.py`
- Modify: `backend/app/integrations/hooks.py`
- Modify: `backend/app/routers/misc.py`
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/App.tsx`
- Modify: `.env.example`
- Create: `backend/tests/test_status.py`

**Interfaces:**
- Produces: integration status `{mode, configured, last_operation, detail}`.
- Produces: CLI exit 0 when required local components are ready, exit 1 otherwise.

- [ ] **Step 1: Write failing status tests**

Test that a configured key with no successful call returns
`configured=True`, `last_operation=None`, and is not labeled verified. Test that
recording a successful execution changes `last_operation` to `"succeeded"` and
a failed execution changes it to `"failed"` without exposing secrets.

- [ ] **Step 2: Run focused tests and verify the current `{gmail: true}` result**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_status.py -q
```

- [ ] **Step 3: Add thread-safe last-operation state**

```python
def status() -> dict:
    return {
        "mode": mode(),
        "configured": is_live(),
        "last_operation": _LAST_OPERATION,
        "detail": _LAST_DETAIL,
    }
```

Update state after every real `execute` success/failure. Detail is a short
sanitized category, never response bodies or credentials.

- [ ] **Step 4: Update UI labels**

Map status to:

- disabled → `○ Google off`
- configured/unverified → `○ Google configured`
- success → `● Google verified`
- failure → `▲ Google error`

Map agent status similarly; never render “Local agent · live” for
`endpoint_disabled`.

- [ ] **Step 5: Implement the doctor command**

The script checks Python, Node, dashboard assets, DB parent writability,
`GET /api/health`, and optionally `POST /api/health/agent-check --live-agent`.
It prints one `PASS`, `WARN`, or `FAIL` line per check and never changes config.

- [ ] **Step 6: Run backend/build/doctor checks and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
cd dashboard && npm run build
.venv/bin/python scripts/doctor.py --help
git add scripts/doctor.py backend/app/integrations/composio_client.py backend/app/integrations/hooks.py backend/app/routers/misc.py backend/tests/test_status.py dashboard/src/api.ts dashboard/src/App.tsx .env.example
git commit -m "feat: add truthful runtime readiness status"
```
