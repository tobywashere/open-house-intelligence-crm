"""Durable delivery for external hooks created by approved CRM proposals.

The approval transaction stores only stable database references. A dispatcher
claims one row in a short transaction, closes the connection, performs the
external work, then records success or failure in another short transaction.

Delivery is at-least-once. A worker can crash after an external provider accepts
a request but before the delivered state commits. The current Composio Calendar
and Gmail actions do not expose a compatible idempotency-key parameter, so a
stale claim is retried with the stable key retained for diagnosis and auditing.
"""
import os
import uuid

from ..db import audit, get_conn, row_to_dict

NOW = "strftime('%Y-%m-%dT%H:%M:%S','now','localtime')"
DEFAULT_STALE_AFTER_SECONDS = 300

_HOOK_BY_OPERATION = {
    "create_lead": "lead_created",
    "book_appointment": "tour_booked",
    "schedule_followup": "reminder_created",
}

_FAILURE_TOOL = {
    "lead_created": "gmail_create_draft (failed)",
    "tour_booked": "gcal_create_event (failed)",
    "reminder_created": "gcal_create_event (failed)",
}


class HookDeliveryError(RuntimeError):
    pass


def enqueue_approval_hook(
    conn, pending_change_id: int, operation: str, result: dict
) -> int | None:
    """Insert one reference-only hook intent in the caller's approval transaction."""
    hook_type = _HOOK_BY_OPERATION.get(operation)
    if hook_type is None:
        return None

    object_id = int(result["id"])
    lead_id = object_id if operation == "create_lead" else int(result["lead_id"])
    idempotency_key = f"pending-change:{pending_change_id}"
    conn.execute(
        "INSERT OR IGNORE INTO hook_outbox "
        "(pending_change_id, idempotency_key, hook_type, object_id, lead_id) "
        "VALUES (?,?,?,?,?)",
        (pending_change_id, idempotency_key, hook_type, object_id, lead_id),
    )
    row = conn.execute(
        "SELECT * FROM hook_outbox WHERE pending_change_id = ?",
        (pending_change_id,),
    ).fetchone()
    if not row:
        raise RuntimeError("approval hook intent was not persisted")
    if (
        row["idempotency_key"] != idempotency_key
        or row["hook_type"] != hook_type
        or row["object_id"] != object_id
        or row["lead_id"] != lead_id
    ):
        raise RuntimeError("approval hook intent conflicts with the approved result")
    return int(row["id"])


def _eligible_sql() -> str:
    return (
        "(status IN ('pending','failed') OR "
        "(status = 'processing' AND "
        "(claimed_at IS NULL OR claimed_at <= "
        "strftime('%Y-%m-%dT%H:%M:%S','now','localtime', ?))))"
    )


def _stale_modifier(stale_after_seconds: int) -> str:
    seconds = max(int(stale_after_seconds), 1)
    return f"-{seconds} seconds"


def _claim(outbox_id: int, stale_after_seconds: int) -> dict | None:
    token = uuid.uuid4().hex
    with get_conn() as conn:
        claimed = conn.execute(
            f"UPDATE hook_outbox SET status = 'processing', claim_token = ?, "
            f"claimed_at = ({NOW}), attempts = attempts + 1, updated_at = ({NOW}) "
            f"WHERE id = ? AND {_eligible_sql()}",
            (token, outbox_id, _stale_modifier(stale_after_seconds)),
        )
        if claimed.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT * FROM hook_outbox WHERE id = ?", (outbox_id,)
        ).fetchone()
        return dict(row)


def _load_hook_arguments(row: dict) -> tuple:
    """Resolve references in a short transaction; never hold it during a hook."""
    with get_conn() as conn:
        if row["hook_type"] == "lead_created":
            lead = conn.execute(
                "SELECT * FROM leads WHERE id = ?", (row["object_id"],)
            ).fetchone()
            if not lead:
                raise HookDeliveryError("lead no longer exists")
            return (row_to_dict(lead),)
        if row["hook_type"] == "tour_booked":
            appt = conn.execute(
                "SELECT * FROM appointments WHERE id = ?", (row["object_id"],)
            ).fetchone()
            if not appt:
                raise HookDeliveryError("appointment no longer exists")
            lead = conn.execute(
                "SELECT * FROM leads WHERE id = ?", (appt["lead_id"],)
            ).fetchone()
            if not lead:
                raise HookDeliveryError("appointment lead no longer exists")
            return row_to_dict(lead), dict(appt)
        if row["hook_type"] == "reminder_created":
            reminder = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (row["object_id"],)
            ).fetchone()
            if not reminder:
                raise HookDeliveryError("reminder no longer exists")
            return (dict(reminder),)
    raise HookDeliveryError(f"unknown hook type {row['hook_type']}")


def _invoke_hook(row: dict, args: tuple) -> None:
    from . import hooks

    hook = {
        "lead_created": hooks.on_lead_created,
        "tour_booked": hooks.on_tour_booked,
        "reminder_created": hooks.on_reminder_created,
    }[row["hook_type"]]
    # Existing test and third-party wrappers sometimes return None on success;
    # only an explicit False from the shipped hooks means delivery failed.
    if hook(*args) is False:
        raise HookDeliveryError("hook reported failure")


def _sanitized_error(exc: Exception) -> str:
    message = str(exc) or type(exc).__name__
    for env_name in (
        "COMPOSIO_API_KEY",
        "OHI_API_TOKEN",
        "OPENCLAW_GATEWAY_TOKEN",
        "OPENCLAW_API_TOKEN",
    ):
        secret = os.environ.get(env_name)
        if secret:
            message = message.replace(secret, "[redacted]")
    return message[:500]


def _mark_failed(row: dict, exc: Exception) -> None:
    error = _sanitized_error(exc)
    with get_conn() as conn:
        updated = conn.execute(
            f"UPDATE hook_outbox SET status = 'failed', last_error = ?, "
            f"claim_token = NULL, claimed_at = NULL, updated_at = ({NOW}) "
            "WHERE id = ? AND status = 'processing' AND claim_token = ?",
            (error, row["id"], row["claim_token"]),
        )
        if updated.rowcount != 1:
            return
        lead_exists = conn.execute(
            "SELECT 1 FROM leads WHERE id = ?", (row["lead_id"],)
        ).fetchone()
        audit(
            conn,
            "user",
            _FAILURE_TOOL[row["hook_type"]],
            {
                "pending_id": row["pending_change_id"],
                "outbox_id": row["id"],
                "idempotency_key": row["idempotency_key"],
            },
            {"error": error, "retryable": True},
            row["lead_id"] if lead_exists else None,
        )


def _mark_delivered(row: dict) -> bool:
    with get_conn() as conn:
        updated = conn.execute(
            f"UPDATE hook_outbox SET status = 'delivered', last_error = NULL, "
            f"claim_token = NULL, claimed_at = NULL, delivered_at = ({NOW}), "
            f"updated_at = ({NOW}) "
            "WHERE id = ? AND status = 'processing' AND claim_token = ?",
            (row["id"], row["claim_token"]),
        )
        return updated.rowcount == 1


def dispatch_hook(
    outbox_id: int, *, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
) -> bool:
    """Claim and attempt one row. Expected delivery failures never raise."""
    row = _claim(outbox_id, stale_after_seconds)
    if row is None:
        return False
    try:
        args = _load_hook_arguments(row)
        _invoke_hook(row, args)
    except Exception as exc:
        try:
            _mark_failed(row, exc)
        except Exception:
            # If recording fails, keep the processing claim. Startup recovery
            # can reclaim it after the stale window instead of losing intent.
            pass
        return False
    try:
        return _mark_delivered(row)
    except Exception:
        # The external action may have succeeded. Leaving a stale processing
        # claim intentionally chooses at-least-once recovery over lost delivery.
        return False


def drain_hook_outbox(
    *, max_items: int = 100, stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS
) -> int:
    """Attempt each outstanding row at most once in this drain invocation."""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM hook_outbox WHERE {_eligible_sql()} ORDER BY id LIMIT ?",
            (_stale_modifier(stale_after_seconds), max(max_items, 0)),
        ).fetchall()
    delivered = 0
    for row in rows:
        delivered += int(
            dispatch_hook(row["id"], stale_after_seconds=stale_after_seconds)
        )
    return delivered
