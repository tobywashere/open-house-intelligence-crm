---
name: daily-brief
description: Fetch three fixed primary sources and publish a short daily brief covering the Seattle job market, the Federal Reserve rate, and recent Seattle community news. Use for the dashboard's "Refresh now" request or requests to generate today's brief. The supported workflow is deterministic and source-validated.
---

# Daily Brief

Build and persist a brief from exactly these URLs:

1. Job market — U.S. Bureau of Labor Statistics
   `https://www.bls.gov/eag/eag.wa_seattle_msa.htm`
2. Federal Reserve rate — latest completed FOMC decision
   `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm`
3. Seattle community — City of Seattle neighborhood funding
   `https://frontporch.seattle.gov/2026/07/07/city-of-seattle-opens-second-round-of-neighborhood-funding-for-community-led-projects/`

## Mode 1: deterministic supported workflow

Make exactly one terminal call:

```bash
{baseDir}/scripts/run_daily_brief.py
```

Do not fetch or publish manually in this mode. The script fetches, extracts,
validates, publishes with the guarded CRM client, and reads the saved report
back for verification.

The command above is the only supported publication workflow for this agent.
The dedicated agent has no general web, network, browser, or filesystem tools;
this runner is one of its two explicitly allowlisted executable entry points.
Do not invoke a general Python interpreter, use a repository-relative path,
write a temporary payload, compose a replacement payload, or call
`/api/summary` directly.

Only successfully parsed source items may appear in the saved brief. If a
source is unavailable, the script records a visible `Sources unavailable`
notice instead of guessing. If every source is unavailable, it saves an empty
market list with that notice. Never replace unavailable information with sample
content or a plausible market update. Each item's `date` must be an explicit
publication or release date from its source. A retrieval date is not a
publication date; omit the item when the source does not provide one.

Completion requires the script to exit `0` and print JSON containing
`"ok": true` and `"published": true`. Report its error if it exits nonzero.
A chat response without this successful command is not completion.
