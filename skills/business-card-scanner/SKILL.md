---
name: business-card-scanner
description: "Extract contact details from business card images and create CRM leads via crm-db-operations."
---

# Business Card Scanner

Scan business card images, extract contact details, and add them as leads in the CRM.

## When to use

The user shares a business card image (photo, screenshot, or file) and says something like:
- "record this lead"
- "add this person"
- "save this card"
- "add to CRM"
- "put them in the database"

## Workflow

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

```python
import sys; sys.path.insert(0, "/home/dell/.openclaw/skills/crm-db-operations"); import tools
all_leads = tools.list_leads()
# Look for exact phone or email match in the returned list
# Also fuzzy name match if you have it
```

If a strong duplicate match is found (same phone or email), report it to the user:

> "Found a matching profile: [Name] (Lead #X). Want me to add more context or merge?"

If no strong match, proceed to create.

### 4. Create the lead

```python
import sys; sys.path.insert(0, "/home/dell/.openclaw/skills/crm-db-operations"); import tools
lead = tools.create_lead(
    name="Jessica Martinez",
    phone="(555) 123-4567",
    email="jessica@leaprealestate.com",
    area="San Diego, CA",
    source="form",
    raw_text="LEAP Real Estate — Real Estate Agent. Specialties: Buying, Selling, Investing, Local Expertise. Website: www.leaprealestate.com. Address: 123 Market St., Suite 200, San Diego, CA 92101"
)
```

### 5. Post-create duplicate check

Use the returned `lead_id` to run a final duplicate check:

```python
duplicates = tools.find_duplicate_leads(lead.lead_id)
if duplicates:
    # Present to user for merge decision
```

### 6. Report back

> Added **Jessica Martinez** to the CRM:
> - Lead #[id]
> - Phone: (555) 123-4567
> - Email: jessica@leaprealestate.com
> - Company: LEAP Real Estate
> - Specialties: Buying, Selling, Investing, Local Expertise
> - No duplicates found.

## Rules

- **Never hallucinate** phone numbers, emails, or names.
- If the image is too blurry or unclear, ask for a clearer photo.
- If the card isn't a business card (receipt, flyer, etc.), say so and skip.
- If duplicates are found, **ask before creating** a second profile.
- If merging is needed, use `merge_leads(primary_id, duplicate_id)` after user confirms.
- Include all extracted context in `raw_text` so the backend has full material for scoring and follow-up generation later.

## Supported card types

- Real estate agent cards (LEAP Real Estate and similar)
- Any generic business card with name, contact info, company
- Multi-contact cards (extract the primary contact; note additional contacts in `raw_text`)
