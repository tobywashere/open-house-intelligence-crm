"""Business-card scan relay (additive, docs/CONTRACT.md §2).

The dashboard uploads a card image; in openclaw mode the agent (which runs on
the same box) extracts the fields with the business-card-scanner skill and
replies with JSON; in mock mode a canned card comes back. The endpoint only
EXTRACTS — the dashboard shows a review step and creates the lead via the
normal POST /leads afterwards (confirm-before-write).
"""
import base64
import binascii
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agent import get_driver
from ..db import audit, get_conn
from ..duplicates import find_duplicate_candidates

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


ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
KIND_TO_EXT = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}


def _sniff_image_kind(image: bytes) -> str | None:
    """Sniff magic bytes so a renamed non-image can't ride the extension
    whitelist through — the extension check alone only validates the name.
    Returns the real kind (or None), which also becomes the stored file's
    extension — a client-supplied "card.png" holding JPEG bytes is stored as
    ".jpg", never trusting the client's claimed extension over the content."""
    if image.startswith(b"\xff\xd8"):
        return "jpeg"
    if image.startswith(b"\x89PNG"):
        return "png"
    if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "webp"
    return None


@router.post("/scan-card")
async def scan_card(body: ScanIn):
    if len(body.data) > 11_000_000:  # ~8 MB decoded
        raise HTTPException(status_code=413, detail=(
            "Image too large — max 8 MB. The dashboard downscales photos "
            "automatically; if uploading a file, resize it first."))
    try:
        image = base64.b64decode(body.data)
    except binascii.Error:
        raise HTTPException(status_code=400, detail="Invalid image payload.")

    claimed_ext = Path(body.filename).suffix.lower()
    if claimed_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=422, detail="not a recognized image")
    kind = _sniff_image_kind(image)
    if kind is None:
        raise HTTPException(status_code=422, detail="not a recognized image")
    ext = KIND_TO_EXT[kind]  # derived from sniffed content, not the client-claimed extension

    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"card-{int(time.time() * 1000)}{ext}"
    path.write_bytes(image)

    # retention: keep the 20 most recent uploads (names embed a ms timestamp)
    for old in sorted(UPLOADS.glob("card-*"))[:-20]:
        try:
            old.unlink()
        except OSError:
            pass

    driver = get_driver()
    extracted: dict = {}
    if driver.name == "openclaw":
        try:
            reply = await driver.chat(
                "A business card image was saved at "
                f"{path.resolve()}. Use the business-card-scanner skill in "
                "EXTRACTION-ONLY mode: read the card and reply with ONLY a JSON "
                "object with keys name, phone, email, area, intent, raw_text "
                "(raw_text = structured summary of everything on the card). Do "
                "NOT create the lead and do NOT call any CRM tools — the user "
                "reviews first.",
                "card-scan",
            )
            start, end = reply.find("{"), reply.rfind("}")
            if start != -1 and end != -1:
                extracted = json.loads(reply[start:end + 1])
        except Exception as exc:  # incl. gateway errors chat() didn't swallow
            logging.warning("card scan extraction failed (%s)", exc)
            extracted = {}
        # fail loudly: no JSON (chat() returns a fallback string on gateway
        # errors) or an all-empty object would give a silent blank review form
        if not any(extracted.get(k) for k in ("name", "phone", "email", "raw_text")):
            with get_conn() as conn:
                audit(conn, "agent", "scan_card",
                      {"filename": body.filename},
                      {"extracted": False, "duplicates": 0})
            raise HTTPException(status_code=502, detail=(
                "The agent couldn't read the card — no structured reply. "
                "Try again, or add the lead manually."))
    else:
        extracted = dict(MOCK_CARD)

    # duplicate pre-check on phone/email so the review step can warn
    with get_conn() as conn:
        duplicates = find_duplicate_candidates(conn, extracted)
        audit(conn, "agent", "scan_card",
              {"filename": body.filename},
              {"extracted": bool(extracted), "duplicates": len(duplicates)})

    # the absolute server path stays server-side (used above for the agent
    # chat message) — never echo it back to the client
    return {"extracted": extracted, "duplicates": duplicates, "filename": path.name}
