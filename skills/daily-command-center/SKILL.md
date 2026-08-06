---
name: daily-command-center
description: Add preparation suggestions for today's real CRM appointments without replacing CRM facts.
---

# Daily Command Center

## Purpose

Prepare a real-estate professional for today's actual appointments. The CRM
backend owns every displayed fact. This skill may add only bounded preparation
suggestions and a recommendation for a real appointment.

## Non-negotiable trust rules

1. Use only `crm-db-operations` tools. Never read or write SQL.
2. Never invent a lead, appointment, time, location, score, preference,
   relationship fact, travel time, market fact, or outstanding response.
3. Never add a plausible appointment or schedule block when the calendar is
   empty.
4. Do not copy sample/example people or facts into a production briefing.
5. If a useful preparation suggestion cannot be grounded in the lead's
   current CRM record, omit it.
6. Agent-written text is advice, not fact. Keep it in `prepare` or
   `recommendation`; do not present it as something the client said.

## Pull today's data

1. Run:

   ```bash
   {baseDir}/../crm-db-operations/cli.py list_appointments --args '{}'
   ```

2. Filter appointments to today's local `YYYY-MM-DD` date using `start_ts`.
3. For every unique `lead_id` in those appointments, run the wrapper with that
   real ID:

   ```bash
   {baseDir}/../crm-db-operations/cli.py get_lead_context --args '{"lead_id":4}'
   ```

4. Use the returned lead fields and events only to decide whether a short
   preparation checklist or recommendation is warranted.

When there are no appointments today, post an empty `meeting_briefs` array.
The backend will display the honest empty schedule.
If either CRM read fails or returns malformed data, do not publish a briefing.
Report the failure instead of guessing about appointments or leads.

## What the backend displays

`GET /api/briefing` rebuilds the visible schedule and factual meeting details
from SQLite on every request:

- appointment time and title;
- lead name and ID;
- area, budget, timeline, intent, and preferences;
- persona and deterministic score;
- due reminders and leads already marked neglected.

The backend ignores any replacement values for those facts. It joins advice
to a real lead and a real appointment.

## Output contract

Post only this shape:

```json
{
  "date": "2026-07-28",
  "generated_at": "2026-07-28T07:00:12",
  "meeting_briefs": [
    {
      "lead_id": 4,
      "prepare": [
        "Review the recorded Bellevue preference before the meeting"
      ],
      "recommendation": "Confirm whether the six-week timeline is still current."
    }
  ]
}
```

Field rules:

- `date`: today's local `YYYY-MM-DD`.
- `generated_at`: when this advice was created.
- `meeting_briefs`: zero or one item per real lead with an appointment today.
- `lead_id`: must be returned by today's `list_appointments()` call.
- `prepare`: at most ten concise suggestions. Every suggestion must be
  traceable to that lead's stored context.
- `recommendation`: one optional suggestion, at most 2,000 characters.

Do not include `schedule`, `name`, `time`, `score`, `summary`,
`suggested_actions`, or other factual replacement fields. Even if included,
the API drops them.

## Publish

Call the wrapper with the complete payload as the named argument:

```bash
{baseDir}/../crm-db-operations/cli.py post_briefing --args '{"payload":{"date":"2026-07-28","generated_at":"2026-07-28T07:00:12","meeting_briefs":[]}}'
```

Replace the example values with today's grounded payload. If the command
returns an error, report it; do not substitute a sample briefing.

After publishing, the dashboard combines this advice with current CRM facts.
If the advice is absent or invalid, the factual CRM schedule still renders
without it.

## Interactive answer

If the user runs this skill in chat, you may summarize today's real
appointments and preparation advice after completing the tool calls. Clearly
say when a requested item is not recorded. Do not estimate travel, availability,
or relationship details unless a tool returned that exact information.
