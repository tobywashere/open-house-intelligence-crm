import logging

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..agent import get_driver
from ..db import get_conn

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatIn(BaseModel):
    message: str
    session_id: str = "dashboard"


@router.post("")
async def chat(body: ChatIn):
    driver = get_driver()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (?,?,?)",
            (body.session_id, "user", body.message),
        )
    # the user turn is already persisted — an exception here would leave it
    # hanging in the history with no reply, so always store something
    try:
        reply = await driver.chat(body.message, body.session_id)
    except Exception:
        logging.exception("chat driver failed")
        reply = "⚠ The agent is unavailable right now. Your message is saved — try again shortly."
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_messages (session_id, role, content) VALUES (?,?,?)",
            (body.session_id, "agent", reply),
        )
    return {"reply": reply, "session_id": body.session_id}


@router.get("/history")
def history(session_id: str = "dashboard", limit: int = Query(50, ge=1, le=500)):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?", (session_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


@router.get("/sessions")
def sessions():
    """Distinct conversations, newest first — powers the chat history picker."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT session_id, COUNT(*) AS message_count, MAX(created_at) AS last_at, "
            "(SELECT content FROM chat_messages c2 WHERE c2.session_id = c.session_id "
            " ORDER BY c2.id DESC LIMIT 1) AS preview "
            "FROM chat_messages c GROUP BY session_id ORDER BY last_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@router.delete("/history")
def clear_history(session_id: str):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    return {"session_id": session_id, "deleted": cur.rowcount}
