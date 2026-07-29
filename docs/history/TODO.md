# Historical post-MVP checklist

This file was the July 2026 team handoff. It is kept in `docs/history/` for
context, not as live feature status. The README and
[`docs/CONTRACT.md`](../CONTRACT.md) are current.

## Completed since the original handoff

- Natural-language CRM reads, creates, updates, reminders, availability
  checks, booking, and explicit won/lost closing are available through the
  `crm-db-operations` skill.
- Business-card scanning uses extraction-only mode and review before create.
- Voice-note intake supports recording/file upload, local OpenClaw
  transcription, editable review, duplicate choices, and confirmation before
  a CRM write.
- Won/lost outcomes are additive fields on the forward-only `closed` status.
  Only explicit wins count as conversion.
- Non-idempotent email/calendar creates are not automatically replayed after
  an ambiguous timeout.
- OpenClaw readiness distinguishes endpoint disabled, unauthorized,
  unreachable, enabled, verified, and failed.
- Briefing schedules and lead facts are always rebuilt from canonical CRM
  rows. The agent may publish only bounded preparation advice.
- Daily market summaries require valid source URLs and have no fabricated
  fallback.
- Knowledge-document controls, vertical packs, research settings, and
  deterministic insights are shipped.

## Still operator/deployment dependent

- Install and configure a tool-capable OpenClaw model.
- Enable OpenClaw's Chat Completions endpoint.
- Verify the configured audio transcription provider on the target Mac mini
  or GB10.
- Install the three bundled CRM/card/daily skills in OpenClaw.
- Configure an OpenClaw schedule if automatic morning advice is desired.
- Configure a separate source-backed market-summary publishing workflow if
  market news is desired.
- Enable Composio only when real Gmail/Google Calendar access is intended.

For acceptance steps, see
[`docs/MAC-MINI-SETUP.md`](../MAC-MINI-SETUP.md) or
[`docs/LOCAL-AI.md`](../LOCAL-AI.md).
