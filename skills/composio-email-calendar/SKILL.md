---
name: composio-email-calendar
description: "Google Calendar AND Gmail through the locally-authed Composio CLI. Calendar: create events, check free/busy, list today's/this week's schedule — 'put it on my calendar', 'am I free Thursday 2pm', 'what's on my calendar', 'block off 3pm', 'schedule a walkthrough'. Email: send, draft, search inbox — 'email this lead', 'draft a follow-up', 'did anyone reply'. Use for ANY calendar or email request. Real external side effects: always confirm before sending mail or creating events."
---

# Composio email + calendar skill

Gives the agent real Gmail and Google Calendar access via the `composio` CLI
already logged in on the GB10 (managed OAuth — never handle tokens or keys).
This is the OpenClaw-side counterpart of the backend's
`INTEGRATIONS_MODE=live` hooks; use it when the user asks *you* to email or
schedule something, not for flows the backend already automates.

⚠ **The connected Google account is the CLI's logged-in account** (currently
zhenkai.kay@gmail.com — check with `composio whoami`). Mail sends from, and
events land on, THAT account.

## Rules

1. **Never send email or create an event without explicit user confirmation**
   of recipient, subject/summary, and content in this conversation. When the
   user's intent is fuzzy, make a **draft** (`create_draft`) instead — drafts
   are always safe.
2. Never invent an email address. Recipients (`to`, `cc`, and `bcc`) must
   already be a lead's email in the CRM (`crm-db-operations` → the lead's
   `email` field) — `send_email` enforces this in code and raises
   `IntegrationError` for anything else, including an address the user gives
   you verbatim if it doesn't match an existing lead. If the recipient isn't
   a lead yet, create the lead first (or use `create_draft`, which has no
   recipient restriction, so a human reviews and sends it).
3. For anything tied to a CRM lead, prefer the backend routes so the closed
   loop stays intact: sending a lead follow-up = `POST /api/email/send`
   (logs the timeline event, flips status, creates the reply-check reminder);
   booking a tour = `book_appointment` (the backend hook mirrors it to GCal
   and stores `gcal_event_id`). Use the direct tools below when the backend
   is unreachable, in off mode, or the task isn't a CRM-lead flow.
4. If you email a lead directly with `send_email`, immediately log an `email`
   event on that lead via `crm-db-operations` with the marker
   `[gmail:<message_id>]` in the content — the reply poller dedupes on it.
5. Check `free_busy` before proposing or booking any time slot. Timezone is
   `GCAL_TIMEZONE` (default America/Los_Angeles); always pass ISO-8601 times.
6. On `IntegrationError`, tell the user plainly what failed and continue —
   never fake success, never retry a **send** blindly (it may have gone out;
   check `fetch_emails("in:sent", 3)` first).

## Setup

```python
import sys
sys.path.insert(0, "/home/dell/.openclaw/skills/composio-email-calendar")
import tools

tools.fetch_emails("newer_than:1d", 5)   # smoke test, read-only
```

Zero pip dependencies; shells out to the `composio` binary. If it raises
"composio CLI not found" or an auth error, run `composio whoami` /
`composio link gmail` in a terminal — do not attempt to fix auth yourself.

## Tool catalog

| Tool | Signature | Use it when... |
|---|---|---|
| `send_email` | `(to, subject, body, *, cc=None, bcc=None)` | User confirmed a real send to an existing lead (`to`/`cc`/`bcc` all checked against `leads.email`), or backend `/email/send` is unavailable (then apply rule 4). Raises on any recipient not already a lead — use `create_draft` for anyone else. |
| `create_draft` | `(to, subject, body)` | Prepare mail for human review — the default when unsure. |
| `fetch_emails` | `(query="in:inbox", max_results=10)` | "Did X reply?", "any new inquiries?" — Gmail search syntax (`from:`, `newer_than:2d`, `is:unread`). |
| `create_event` | `(summary, start_datetime, *, duration_minutes=30, description="", location="", attendees=None, timezone=..., calendar_id="primary")` | Confirmed calendar block not tied to a CRM appointment. Returns the event (store `id` if CRM-related). |
| `free_busy` | `(time_min, time_max, calendar_ids=None)` | Before offering/booking any slot. |
| `list_events` | `(time_min, time_max, calendar_id="primary")` | "What's on my calendar today/this week?" |

`tools.execute(slug, args)` enforces a hard allowlist (`tools.ALLOWED_SLUGS`) —
only the slugs the table above actually uses. Any other slug (including
destructive ones like `GMAIL_DELETE_MESSAGE`, or a slug beyond this catalog)
raises `IntegrationError` before it reaches the CLI, even if you construct
the call yourself. To use more Gmail/GCal actions, discover them with
`composio search "<task>"`, inspect inputs with
`composio execute <SLUG> --get-schema`, get the slug added to
`ALLOWED_SLUGS` in `tools.py`, then call `tools.execute(slug, args)`. The
confirmation rules above apply to ALL write actions, catalog or not.

`send_email` additionally refuses to send if `to`, or any address in `cc`/
`bcc`, isn't (case-insensitively) an existing lead's email — it checks via
`crm-db-operations`' `list_leads`. Use `create_draft` for anyone not yet in
the CRM.

⚠ **This is a blast-radius reducer, not an exfiltration stop.** The lead set
it checks against is not a fixed, human-curated allowlist — it's whatever's
currently in `leads.email`, and the agent itself can extend that set: it can
`create_lead(email=...)` with an attacker-supplied address and then send to
it, and the inbox poller (when enabled) auto-intakes unknown senders as
leads, which would then also pass this check. The guard stops a *casual*
mistake (a hallucinated or copy-pasted stray address) and narrows where a
compromised prompt can send mail to "addresses that made it into the CRM
somehow" — it does not stop a determined prompt-injection that first gets
its own address added as a lead. Don't rely on it as the only control against
data exfiltration via email.
