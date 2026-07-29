---
name: daily-brief
description: Fetch three fixed primary sources and publish a short daily brief covering the Seattle job market, the Federal Reserve rate, and recent Seattle community news. Use for the dashboard's "Refresh now" request or any request to generate today's daily brief.
---

# Daily Brief

Build and persist a short daily brief from exactly these three URLs:

1. Job market — U.S. Bureau of Labor Statistics  
   `https://www.bls.gov/eag/eag.wa_seattle_msa.htm`
2. Federal Reserve rate — latest completed FOMC decision  
   `https://www.federalreserve.gov/newsevents/pressreleases/monetary20260617a.htm`
3. Seattle community — City of Seattle neighborhood funding  
   `https://frontporch.seattle.gov/2026/07/07/city-of-seattle-opens-second-round-of-neighborhood-funding-for-community-led-projects/`

## Run

For every scheduled run or dashboard refresh, make exactly one terminal call:

```bash
python3 skills/daily-brief/scripts/run_daily_brief.py
```

Do not fetch or publish the sources manually. The bundled stdlib-only script:

1. Fetches all three URLs.
2. Extracts only facts present in the pages.
3. Builds the dashboard's exact `market_watch` and `ai_insights` payload.
4. Validates all required fields.
5. Auto-detects the CRM backend (`CRM_API_URL`, then local ports 8000/8080).
6. Calls `POST /api/summary`.
7. Reads `GET /api/summary?date=...` back and verifies `generated_at`.

Completion requires the script to exit `0` and print JSON containing
`"ok": true` and `"published": true`. Report its error if it exits nonzero.
A chat response without this successful command is not completion.
