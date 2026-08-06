"""Durable delivery for external hooks created by approved CRM proposals.

The approval transaction stores only stable database references. A dispatcher
claims one row in a short transaction, closes the connection, performs the
external work, then records success or failure in another short transaction.

Delivery is at-least-once. A worker can crash after an external provider accepts
a request but before the delivered state commits. The current Composio Calendar
and Gmail actions do not expose a compatible idempotency-key parameter, so a
stale claim is retried with the stable key retained for diagnosis and auditing.
"""
import logging
import os
import threading
import uuid

from ..db import audit, get_conn, row_to_dict

NOW = "strftime('%Y-%m-%dT%H:%M:%S','now','localtime')"
DEFAULT_STALE_AFTER_SECONDS = 300
DEFAULT_BATCH_SIZE = 100
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_RETRY_BASE_SECONDS = 5
DEFAULT_RETRY_MAX_SECONDS = 300

_HOOK_BY_OPERATION = {
    "create_lead": "lead_created",
    "book_appointment": "tour_booked",
    "schedule_followup": "reminder_created",
}

_FAILURE_TOOL = {
    "lead_created": "lead_created_hook (failed)",
    "tour_booked": "gcal_create_event (failed)",
    "reminder_created": "gcal_create_event (failed)",
}

_WORKER_LOCK = threading.Lock()
_worker_thread: threading.Thread | None = None
_worker_stop_event: threading.Event | None = None
_worker_wake_event: threading.Event | None = None


class HookDeliveryError(RuntimeError):
    pass


def enqueue_approval_hook(
    conn,
    pending_change_id: int,
    operation: str,
    result: dict,
    *,
    delivery_mode: str,
) -> int | None:
    """Insert one reference-only hook intent in the caller's approval transaction."""
    hook_type = _HOOK_BY_OPERATION.get(operation)
    if hook_type is None:
        return None

    object_id = int(result["id"])
    lead_id = object_id if operation == "create_lead" else int(result["lead_id"])
    idempotency_key = f"pending-change:{pending_change_id}"
    if delivery_mode not in {"live", "simulated"}:
        raise ValueError(f"unknown approval hook delivery mode {delivery_mode}")
    conn.execute(
        "INSERT OR IGNORE INTO hook_outbox "
        "(pending_change_id, idempotency_key, hook_type, object_id, lead_id, "
        "delivery_mode) VALUES (?,?,?,?,?,?)",
        (
            pending_change_id,
            idempotency_key,
            hook_type,
            object_id,
            lead_id,
            delivery_mode,
        ),
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
        or row["delivery_mode"] != delivery_mode
    ):
        raise RuntimeError("approval hook intent conflicts with the approved result")
    return int(row["id"])


def _eligible_sql() -> str:
    return (
        "(status = 'pending' OR "
        "(status = 'failed' AND "
        f"(next_attempt_at IS NULL OR next_attempt_at <= ({NOW}))) OR "
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
    from . import composio_client as cc
    from . import hooks

    hook = {
        "lead_created": hooks.on_lead_created,
        "tour_booked": hooks.on_tour_booked,
        "reminder_created": hooks.on_reminder_created,
    }[row["hook_type"]]
    if row["delivery_mode"] == "simulated":
        outcome = hook(*args, force_simulated=True)
        if outcome is not hooks.HookOutcome.SIMULATED:
            raise HookDeliveryError(
                f"simulated hook returned invalid outcome {outcome!r}"
            )
        return
    if row["delivery_mode"] != "live":
        raise HookDeliveryError(
            f"unknown hook delivery mode {row['delivery_mode']}"
        )
    if not cc.is_live():
        raise HookDeliveryError("live integrations unavailable; delivery deferred")
    outcome = hook(*args)
    if outcome is hooks.HookOutcome.SIMULATED:
        raise HookDeliveryError("live hook returned simulated delivery")
    if outcome is hooks.HookOutcome.FAILED:
        raise HookDeliveryError("hook reported failure")
    if outcome is not hooks.HookOutcome.LIVE_DELIVERED:
        raise HookDeliveryError(f"hook returned invalid outcome {outcome!r}")


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


def _retry_delay(
    attempts: int, retry_base_seconds: int, retry_max_seconds: int
) -> int:
    base = max(int(retry_base_seconds), 0)
    ceiling = max(int(retry_max_seconds), base)
    exponent = min(max(int(attempts) - 1, 0), 16)
    return min(base * (2 ** exponent), ceiling)


def _audit_failure(row: dict, error: str) -> None:
    with get_conn() as conn:
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


def _mark_failed(
    row: dict,
    exc: Exception,
    *,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> bool:
    error = _sanitized_error(exc)
    delay = _retry_delay(
        row["attempts"], retry_base_seconds, retry_max_seconds
    )
    with get_conn() as conn:
        updated = conn.execute(
            f"UPDATE hook_outbox SET status = 'failed', last_error = ?, "
            f"claim_token = NULL, claimed_at = NULL, "
            f"next_attempt_at = strftime('%Y-%m-%dT%H:%M:%S','now','localtime', ?), "
            f"updated_at = ({NOW}) "
            "WHERE id = ? AND status = 'processing' AND claim_token = ?",
            (error, f"+{delay} seconds", row["id"], row["claim_token"]),
        )
        if updated.rowcount != 1:
            return False
    try:
        _audit_failure(row, error)
    except Exception:
        logging.exception(
            "could not persist failure audit for hook outbox row %s",
            row["id"],
        )
    return True


def _mark_delivered(row: dict) -> bool:
    with get_conn() as conn:
        updated = conn.execute(
            f"UPDATE hook_outbox SET status = 'delivered', last_error = NULL, "
            f"claim_token = NULL, claimed_at = NULL, delivered_at = ({NOW}), "
            "next_attempt_at = NULL, "
            f"updated_at = ({NOW}) "
            "WHERE id = ? AND status = 'processing' AND claim_token = ?",
            (row["id"], row["claim_token"]),
        )
        return updated.rowcount == 1


def dispatch_hook(
    outbox_id: int,
    *,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
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
            _mark_failed(
                row,
                exc,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
        except Exception:
            # If recording fails, keep the processing claim. Startup recovery
            # can reclaim it after the stale window instead of losing intent.
            logging.exception(
                "could not persist failed state for hook outbox row %s",
                row["id"],
            )
        return False
    try:
        return _mark_delivered(row)
    except Exception:
        # The external action may have succeeded. Leaving a stale processing
        # claim intentionally chooses at-least-once recovery over lost delivery.
        logging.exception(
            "could not persist delivered state for hook outbox row %s",
            row["id"],
        )
        return False


def drain_hook_outbox(
    *,
    max_items: int = DEFAULT_BATCH_SIZE,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
) -> int:
    """Attempt one eligible batch and return how many rows were selected."""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id FROM hook_outbox WHERE {_eligible_sql()} ORDER BY id LIMIT ?",
            (_stale_modifier(stale_after_seconds), max(max_items, 0)),
        ).fetchall()
    for row in rows:
        try:
            dispatch_hook(
                row["id"],
                stale_after_seconds=stale_after_seconds,
                retry_base_seconds=retry_base_seconds,
                retry_max_seconds=retry_max_seconds,
            )
        except Exception:
            logging.exception(
                "unexpected hook outbox dispatch failure for row %s", row["id"]
            )
    return len(rows)


def _worker_loop(
    stop_event: threading.Event,
    wake_event: threading.Event,
    *,
    batch_size: int,
    poll_seconds: float,
    stale_after_seconds: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> None:
    while not stop_event.is_set():
        # Clear before draining so a notification arriving during a provider
        # call remains set and causes another immediate pass.
        wake_event.clear()
        try:
            while not stop_event.is_set():
                selected = drain_hook_outbox(
                    max_items=batch_size,
                    stale_after_seconds=stale_after_seconds,
                    retry_base_seconds=retry_base_seconds,
                    retry_max_seconds=retry_max_seconds,
                )
                if selected < batch_size:
                    break
        except Exception:
            logging.exception("hook outbox worker drain failed")
        if not stop_event.is_set():
            wake_event.wait(max(float(poll_seconds), 0.01))


def start_worker(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    retry_base_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds: int = DEFAULT_RETRY_MAX_SECONDS,
) -> threading.Thread:
    """Start the single process-local delivery worker, or return the live one."""
    global _worker_thread, _worker_stop_event, _worker_wake_event
    safe_retry_base = max(int(retry_base_seconds), 1)
    safe_retry_max = max(int(retry_max_seconds), safe_retry_base)
    while True:
        stopping_thread = None
        with _WORKER_LOCK:
            if _worker_thread is not None and _worker_thread.is_alive():
                if (
                    _worker_stop_event is None
                    or not _worker_stop_event.is_set()
                ):
                    return _worker_thread
                stopping_thread = _worker_thread
            else:
                stop_event = threading.Event()
                wake_event = threading.Event()
                thread = threading.Thread(
                    target=_worker_loop,
                    kwargs={
                        "stop_event": stop_event,
                        "wake_event": wake_event,
                        "batch_size": max(int(batch_size), 1),
                        "poll_seconds": poll_seconds,
                        "stale_after_seconds": stale_after_seconds,
                        "retry_base_seconds": safe_retry_base,
                        "retry_max_seconds": safe_retry_max,
                    },
                    name="approval-hook-outbox",
                    daemon=True,
                )
                _worker_stop_event = stop_event
                _worker_wake_event = wake_event
                _worker_thread = thread
                thread.start()
                return thread
        # A concurrent shutdown owns this live thread. Wait outside the lock
        # so only a fresh worker can be returned to the restarting app.
        stopping_thread.join()


def wake_worker() -> bool:
    """Wake the managed worker after a transaction commits new intent."""
    with _WORKER_LOCK:
        wake_event = _worker_wake_event
        thread = _worker_thread
    if wake_event is None or thread is None or not thread.is_alive():
        return False
    wake_event.set()
    return True


def stop_worker(*, timeout: float | None = None) -> bool:
    """Signal and join the worker. FastAPI shutdown waits for in-flight work."""
    global _worker_thread, _worker_stop_event, _worker_wake_event
    with _WORKER_LOCK:
        thread = _worker_thread
        stop_event = _worker_stop_event
        wake_event = _worker_wake_event
        if stop_event is not None:
            stop_event.set()
        if wake_event is not None:
            wake_event.set()
    if thread is None:
        return True
    thread.join(None if timeout is None else max(float(timeout), 0.0))
    stopped = not thread.is_alive()
    if stopped:
        with _WORKER_LOCK:
            if _worker_thread is thread:
                _worker_thread = None
                _worker_stop_event = None
                _worker_wake_event = None
    else:
        logging.error("hook outbox worker did not stop before timeout")
    return stopped
