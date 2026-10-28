# Authentication and migration

The API requires two distinct credentials for real CRM use. Missing credentials
and single-token configurations fail at startup; removing credentials while the
server is running fails closed on protected requests. Health remains a public
probe, and CORS preflight does not authorize an operation.

## Native OpenClaw

Use [the native workflow guide](NATIVE-CREATE-LEAD.md). Keep `AGENT_MODE=mock`
in the backend: the native routes dispatch real inference through separate
OpenClaw profiles. Give only `OHI_AGENT_API_TOKEN` to those profiles.

Both `OHI_API_TOKEN` and `OHI_AGENT_API_TOKEN` must be distinct printable ASCII
secrets of at least 32 characters. Generate each separately, for example with
`python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`, and store them
privately. Unlock the dashboard with the human key. Never paste keys into a URL,
commit them, or give the human key to an agent.

With both credentials configured, `bash scripts/serve.sh` validates authentication
before creating data or launching services. It binds to loopback by default.
Changing `HOST` does not turn authentication off.

## Explicit demo

```bash
bash scripts/dev.sh --demo
```

This selects mock inference, disables integrations and polling, binds to loopback,
and uses `backend/data/demo.db`, separate from the ordinary CRM database. Existing
CRM data is not seeded or replaced. Use only synthetic information in the demo.

For a direct backend launch, tokenless access requires `OHI_DEMO_MODE=1`,
`AGENT_MODE=mock`, `INTEGRATIONS_MODE=off`, and a loopback `HOST`. The API also
checks the request peer, Host, and any Origin. These checks do not authenticate
other local processes or users. Do not forward, tunnel, or reverse-proxy demo
mode; proxies can make remote clients appear local. Supported launchers disable
forwarded-header processing.

## Legacy migration

The general `AGENT_MODE=openclaw` driver is disabled even if both keys are set.
Its old CRM tool and setup helper provisioned the human key to the agent, and
`X-Actor` was a caller-selected label, not proof of identity. Adding a second key
to that old configuration does not repair its privilege boundary.

Stop using the legacy gateway profile and helper for real CRM access. If a human
key was previously shared with that profile, rotate it, remove the old profile's
access to it, and configure the separate native profiles. Existing human-key
holders retain human authority until you rotate the key. No automatic rotation
or edits to existing OpenClaw profiles are performed by this change.

In capability mode, agent privileges come from the authenticated key; changing or
omitting `X-Actor` cannot grant approval or direct-write permission. The legacy
header behavior remains only for simulated workflows in explicit demo mode.
