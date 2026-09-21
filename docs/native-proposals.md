# Local native lead proposals

Native create-lead proposals require capability authentication. The backend uses
`OHI_API_TOKEN` for the human and a distinct `OHI_AGENT_API_TOKEN` for the agent.
Use fresh generated secrets of at least 32 printable non-whitespace ASCII
characters. The gateway must receive only the agent CRM token and its own gateway
credential. Do not place the human token in gateway environment, plugin config,
model context, frontend build variables, or a URL.

Backend transport configuration:

```dotenv
NATIVE_PROPOSAL_GATEWAY_URL=http://127.0.0.1:18879
NATIVE_PROPOSAL_GATEWAY_TOKEN=<gateway-only-secret>
NATIVE_PROPOSAL_AGENT_ID=native-proposals
```

The gateway URL must be an HTTP loopback origin without credentials, path, query,
or fragment. The agent ID defaults to `native-proposals` and allows lowercase
ASCII letters, numbers, hyphen and underscore (1–64 characters, initial letter or
number). Each fresh request issues one ordinary Chat Completions call to this
agent, with user `ohi-propose-<request_id>`, no caller-supplied tools, and a total
60-second bound. There are no retries or production mock-provider switches.
Native proposal and read HTTP clients ignore ambient proxy settings and reject
redirects. Completion response text is streamed with a 2 MB bound and discarded.

Install `openclaw-plugins/openhouse-proposals` using the same pinned runtime as
`openhouse-read`. Its SDK entry and manifest follow that reference plugin. Configure
`agentId` to match the backend and `crmApiUrl` to a fixed loopback `/api` URL such
as `http://127.0.0.1:8000/api`. Restrict the dedicated agent to the single
`openhouse_propose_lead` tool. It accepts only `name` and optional `email`/`phone`.
The handler verifies runtime `agentId` and the exact session key
`agent:<agent_id>:openai-user:ohi-propose-<request_id>`. It registers only when a
valid `OHI_AGENT_API_TOKEN` is available and **no `OHI_API_TOKEN` environment key is
present**, even an empty one. Its direct HTTP POST rejects redirects, limits
responses to 64 KiB, and has an absolute 10-second bound. It exposes no approval or
lead-write operation. Actual OpenClaw/Ollama inference acceptance is separate;
these unit/integration tests do not certify a live model run.

## API and recovery

All IDs are exactly 32 lowercase hexadecimal characters. Headers use
`X-API-Token`; `X-Actor` cannot change the credential's capability.

- Human: `POST /api/chat/lead-proposal` with `{request_id, message}`. The original
  message is stored exactly, with length 1–2000. Replays of the same ID/text return
  status without another completion. Changed text, including whitespace changes,
  conflicts. Gateway configuration is checked before reserving a fresh ID; an
  existing request remains readable/replayable with unavailable configuration.
- Human: `GET /api/chat/lead-proposal/{request_id}` returns durable status without
  dispatch. Use an explicit status check to recover an uncertain request.
- Agent: `POST /api/agent/lead-proposals` with `{request_id, name, email?, phone?}`
  queues fields only for an existing running/unknown request. Name is 1–200
  characters and nonempty after trimming; email is at most 320 and phone at most
  100. All must be strings; nulls/extra fields are rejected. Fields are trimmed;
  omission differs from an explicitly empty optional field. Repeated normalized
  fields return the same proposal, including after approval/denial; changed fields
  conflict. No extraction model is called at this endpoint.

Successful responses are HTTP 200:

```json
{"request_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","state":"unknown","proposal":null}
```

`state` is `running`, `unknown`, `failed`, `proposed`, `approved`, or `denied`.
`proposal` is null until it is the existing parsed `PendingChange` database row.
Approval exposes the stored `proposal.result`, with the created lead ID. Terminal
states derive from the pending row; they are not duplicated in the request table.
The existing human pending-change edit/approve/deny endpoints perform all decisions.

A completed call without a stored proposal is `failed`. Timeout, transport/error,
or cancellation without a stored proposal leaves `unknown`; late tool submissions
can settle it. Startup changes unbound running rows to unknown. A stored proposal
always wins over completion failures or generated claims. Keep the request ID
while checking status; never automatically create a new ID for an uncertain call.
Cancellation cleanup persists unknown and re-raises cancellation.

Protocol errors are sanitized `{error: {code, message}}` objects:

| HTTP | Code | Meaning |
| --- | --- | --- |
| 422 | `invalid_request` | Invalid ID, body, type, length, or extra field; input is not echoed. |
| 503 | `not_configured` | A fresh request was not reserved or dispatched. |
| 404 | `not_found` | No durable request exists for that ID. |
| 409 | `request_conflict` | ID is already bound to different original text. |
| 409 | `proposal_conflict` | Stored original proposed fields differ. |
| 409 | `request_settled` | An unbound failed request cannot accept a proposal, or its atomic settlement failed. |

Authentication errors retain the shared Task 1 `{detail: ...}` contract (401
invalid/missing token, 403 wrong capability or capability mode required, 503 invalid
capability configuration). Transport uncertainty is a 200 durable `unknown`
response, not `not_configured`. Never show a created lead from completion prose.

## Test fixture seam

Replace the async module function
`app.native_proposals.complete_native(request_id, message, config)` in a disposable
test process. `config` is `(gateway_url, gateway_token, agent_id)`; return values
are ignored. A synthetic test completion can call the actual agent HTTP endpoint
using the generated agent token or call
`submit_proposal(ProposalIn(request_id=..., name=..., email=...))` in-process.
`propose_lead` still performs real reservation, durable status resolution, and
approval plumbing. Configure a disposable `DB_PATH` before import/startup and
use generated human/agent tokens with `AGENT_MODE=mock`, `INTEGRATIONS_MODE=off`,
and `INTEGRATIONS_POLLER=off`. This seam is not a production provider mode.
