from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import audit, get_conn
from ..routers.leads import NOW, fetch_lead
from . import composio_client as cc

router = APIRouter(tags=["integrations"])


class EmailIn(BaseModel):
    lead_id: int
    subject: str
    body: str


@router.get("/integrations/status")
def status():
    live = cc.is_live()
    return {"mode": cc.mode(), "gmail": live, "gcal": live}


def _recipient_is_known_lead(email: str) -> bool:
    """Case-insensitive check that `email` matches an existing leads.email row.
    Own short get_conn() block — never open across the cc.execute() call."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT 1 FROM leads WHERE lower(email) = lower(?)", (email,)
        ).fetchone() is not None


@router.post("/email/send")
def send_email(body: EmailIn):
    with get_conn() as conn:
        lead = fetch_lead(conn, body.lead_id)
    if not lead.get("email"):
        raise HTTPException(400, "lead has no email address")
    if not _recipient_is_known_lead(lead["email"]):
        raise HTTPException(400, "recipient does not match a known lead")

    simulated = not cc.is_live()
    marker = ""
    if not simulated:
        try:
            data = cc.execute("GMAIL_SEND_EMAIL", {
                "recipient_email": lead["email"],
                "subject": body.subject,
                "body": body.body,
            })
            msg_id = (data.get("response_data") or data).get("id")
            if msg_id:
                marker = f"\n[gmail:{msg_id}]"
        except cc.IntegrationError as e:
            raise HTTPException(502, f"Gmail send failed: {e}")

    # closed loop only after a confirmed (or simulated) send
    with get_conn() as conn:
        lead = fetch_lead(conn, lead["id"])
        conn.execute(
            "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
            (lead["id"], "email",
             f"Email sent: {body.subject}\n\n{body.body}{marker}"))
        if lead["status"] == "new":
            conn.execute(
                f"UPDATE leads SET status = 'contacted', last_activity_at = ({NOW}) "
                "WHERE id = ?", (lead["id"],))
            conn.execute(
                "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
                (lead["id"], "status_change", "new → contacted"))
        due = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute(
            "INSERT INTO reminders (lead_id, due_ts, note) VALUES (?,?,?)",
            (lead["id"], due, f"Check for a reply from {lead['name']}"))
        conn.execute(
            f"UPDATE leads SET last_activity_at = ({NOW}) WHERE id = ?", (lead["id"],))
        audit(conn, "user", "gmail_send" + (" (simulated)" if simulated else ""),
              {"lead_id": lead["id"], "subject": body.subject},
              {"simulated": simulated}, lead["id"])
    return {"sent": True, "simulated": simulated}
