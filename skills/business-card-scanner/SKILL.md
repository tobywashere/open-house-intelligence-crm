---
name: business-card-scanner
description: "Extract contact details from business card images — either reply with extraction-only JSON for the backend scan relay, or create a CRM lead via crm-db-operations when a user shares a card directly."
---

# Business Card Scanner

Scan business card images and extract contact details. Depending on who is asking, either return the extracted fields as JSON, or create a lead in the CRM.

## STEP 0 — Choose the mode (do this FIRST)

There are TWO modes. Pick exactly one before doing anything else.

**Use EXTRACTION-ONLY MODE if ANY of these is true:**
- The request says "EXTRACTION ONLY" or "extraction-only mode".
- The request comes from the backend scan relay (session "card-scan").

**Use FULL MODE only if:**
- A user directly shares a card image in chat and asks to add/record/save the person.

If in doubt, use extraction-only mode. Creating a lead in extraction-only mode causes a DUPLICATE, because the backend creates the lead itself after human review.

## Extraction-only mode

1. Read the business card image. Extract all visible text.
2. Reply with ONLY a single JSON object. Allowed keys: `name`, `phone`, `email`, `area`, `intent`, `raw_text`.

Example reply (the ENTIRE reply, nothing else):

{"name": "Jessica Martinez", "phone": "(555) 123-4567", "email": "jessica@leaprealestate.com", "area": "San Diego, CA", "intent": "sell", "raw_text": "LEAP Real Estate — Real Estate Agent. Specialties: Buying, Selling, Investing. Website: www.leaprealestate.com. Address: 123 Market St., Suite 200, San Diego, CA 92101"}

Hard rules for this mode:
- Output ONLY the JSON object. No prose before or after. No markdown fences.
- Do NOT call any tools. Do NOT run any code. Do NOT import `tools`.
- Do NOT create a lead. Do NOT duplicate-check. The backend does its own duplicate pre-check and creates the lead after human review.
- Omit any key you cannot read from the card. Never invent values.
- If the image is unreadable, reply with exactly `{"raw_text": "unreadable: <reason>"}` and nothing else.

**The workflow ENDS after the JSON reply. STOP. There is no step 3 in this mode.**

## Full mode

Use only when a user directly shares a card image in chat and asks to add the person.

### 1. Extract the card

Use the `image` tool to analyze the business card image. Read all visible text:

- **Full name** — first and last
- **Job title / role**
- **Company name**
- **Phone number(s)**
- **Email address(es)**
- **Website / URL**
- **Mailing address**
- **Specializations or services** (e.g., "Buying", "Selling", "Investing")
- **Tagline, social handles, QR codes** — anything else notable

### 2. Parse into CRM fields

| CRM field | Source |
|---|---|
| `name` | Full name from card |
| `phone` | Phone number, cleaned |
| `email` | Email address |
| `area` | City/state extracted from address |
| `source` | `"form"` (scanned card) |
| `raw_text` | Structured summary of ALL extracted fields |
| `intent` | Inferred from title/specializations if clear |

**Never invent a field value.** If the card has no phone, skip `phone`. No email, skip `email`.

### 3. Pre-check for duplicates (by phone or email)

Before creating the lead, search existing leads to see if this person is already in the CRM:

```json
{"operation":"list_leads","arguments":{"sort":"priority"}}
```

Call the registered `openhouse_crm` tool with that input. Do not call the
`crm-db-operations` skill name as a tool and do not use `exec` for this CRM read.

Look for an exact phone or email match in the returned result. Also consider a
close name match, but do not treat a name alone as proof that two people are
the same.

If a strong duplicate match is found (same phone or email), report it to the user:

> "Found a matching profile: [Name] (Lead #X). Want me to add more context or merge?"

If no strong match, proceed to create.

### 4. Create the lead

Call `openhouse_crm` with:

```json
{"operation":"create_lead","arguments":{"name":"Jessica Martinez","phone":"(555) 123-4567","email":"jessica@leaprealestate.com","area":"San Diego, CA","source":"form","raw_text":"LEAP Real Estate. Specialties: Buying, Selling, Investing. Website: www.leaprealestate.com. Address: 123 Market St., Suite 200, San Diego, CA 92101"}}
```

The CRM returns a pending-change record for human review. Do not claim the lead
was created, and do not run a post-create duplicate check because there is no
new lead ID until the operator approves the change.

### 5. Report back

> Queued **Jessica Martinez** for review:
> - Phone: (555) 123-4567
> - Email: jessica@leaprealestate.com
> - Company: LEAP Real Estate
> - Status: waiting for approval

## Rules

Both modes:
- **Never hallucinate** phone numbers, emails, or names. Skip fields you cannot read.
- If the card isn't a business card (receipt, flyer, etc.), say so and skip. In extraction-only mode, reply `{"raw_text": "unreadable: not a business card"}`.
- Include all extracted context in `raw_text` so the backend has full material for scoring and follow-up generation later.

Extraction-only mode:
- Never call tools, never create leads, never duplicate-check. Reply with the JSON object and stop.

Full mode only:
- If the image is too blurry or unclear, ask for a clearer photo.
- If duplicates are found, **ask before creating** a second profile.
- If merging is needed, use `merge_leads(primary_id, duplicate_id)` after user confirms.

## Supported card types

- Real estate agent cards (LEAP Real Estate and similar)
- Any generic business card with name, contact info, company
- Multi-contact cards (extract the primary contact; note additional contacts in `raw_text`)
