"""Business-card scan relay (additive, docs/CONTRACT.md §2).

The dashboard uploads a card image; in openclaw mode the agent (which runs on
the same box) extracts the fields with the business-card-scanner skill and
replies with JSON; in mock mode a canned card comes back. The endpoint only
EXTRACTS — the dashboard shows a review step and creates the lead via the
normal POST /leads afterwards (confirm-before-write).
"""
import base64
import json
import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from ..agent import get_driver
from ..db import audit, get_conn, row_to_dict

router = APIRouter(tags=["scan"])

UPLOADS = Path(__file__).resolve().parent.parent.parent / "data" / "uploads"

MOCK_CARD = {
    "name": "Jordan Alvarez",
    "phone": "+14255550177",
    "email": "jordan.alvarez@windermere.com",
    "area": "Bellevue",
    "intent": "sell",
    "raw_text": "Business card: Jordan Alvarez — Broker, Windermere Real Estate, Bellevue WA. "
                "Services: Buying · Selling · Investing. [mock scan]",
}


class ScanIn(BaseModel):
    filename: str
    data: str  # base64-encoded image bytes


@router.post("/scan-card")
async def scan_card(body: ScanIn):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    ext = Path(body.filename).suffix.lower() or ".jpg"
    path = UPLOADS / f"card-{int(time.time() * 1000)}{ext}"
    path.write_bytes(base64.b64decode(body.data))

    driver = get_driver()
    extracted: dict = {}
    if driver.name == "openclaw":
        try:
            reply = await driver.chat(
                "A business card image was saved at "
                f"{path.resolve()} . Using the business-card-scanner skill, do the "
                "EXTRACTION ONLY (steps 1-2): read the card and reply with ONLY a "
                "JSON object with keys name, phone, email, area, intent, raw_text "
                "(raw_text = structured summary of everything on the card). Do NOT "
                "create the lead — the user reviews first.",
                "card-scan",
            )
            start, end = reply.find("{"), reply.rfind("}")
            if start != -1 and end != -1:
                extracted = json.loads(reply[start:end + 1])
        except Exception as exc:  # never break the flow — dashboard shows editable fields
            logging.warning("card scan extraction failed (%s)", exc)
    else:
        extracted = dict(MOCK_CARD)

    # duplicate pre-check on phone/email so the review step can warn
    duplicates = []
    phone = re.sub(r"[^\d+]", "", extracted.get("phone") or "") or None
    email = (extracted.get("email") or "").strip().lower() or None
    with get_conn() as conn:
        if phone or email:
            for r in conn.execute("SELECT * FROM leads"):
                lead = row_to_dict(r)
                if phone and re.sub(r"[^\d+]", "", lead.get("phone") or "") == phone:
                    duplicates.append({"lead": lead, "match_on": "phone"})
                elif email and (lead.get("email") or "").lower() == email:
                    duplicates.append({"lead": lead, "match_on": "email"})
        audit(conn, "agent", "scan_card",
              {"filename": body.filename},
              {"extracted": bool(extracted), "duplicates": len(duplicates)})

    return {"extracted": extracted, "duplicates": duplicates, "image": str(path)}
