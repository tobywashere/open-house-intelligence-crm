"""Date-keyed generated content: Executive Briefing, dashboard Insights, and the
Daily Summary overlay. All three follow the same shape — one JSON payload per
date, upserted by the agent (K's crons) and read by the dashboard. The backend
doesn't interpret the payload; it only stores and serves it by date.

Shapes: docs/BRIEFING-UI.md (briefing, summary), docs/INSIGHTS.md (insights).
"""
import json

from fastapi import APIRouter, Body, HTTPException

from ..db import audit, get_conn
from ..report_models import BriefingPost, DailySummaryPost

router = APIRouter(tags=["reports"])


def _fetch(table: str, date: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(f"SELECT payload FROM {table} WHERE date = ?", (date,)).fetchone()
    if not row:
        raise HTTPException(404, f"no {table} generated for {date}")
    return json.loads(row["payload"])


def _upsert(table: str, ts_col: str, payload: dict) -> dict:
    date = payload.get("date")
    if not date:
        raise HTTPException(400, "payload must include a 'date' field")
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO {table} (date, payload) VALUES (?, ?) "
            f"ON CONFLICT(date) DO UPDATE SET payload = excluded.payload, "
            f"{ts_col} = (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))",
            (date, json.dumps(payload)),
        )
        audit(conn, "agent", f"post_{table}", {"date": date}, {})
    return payload


@router.get("/briefing")
def get_briefing(date: str):
    return _fetch("briefing", date)


@router.post("/briefing")
def post_briefing(body: BriefingPost):
    payload = body.model_dump(mode="json")
    lead_ids = {brief["lead_id"] for brief in payload["meeting_briefs"]}
    if lead_ids:
        marks = ",".join("?" for _ in lead_ids)
        with get_conn() as conn:
            existing = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM leads WHERE id IN ({marks})",
                    tuple(sorted(lead_ids)),
                )
            }
        unknown = sorted(lead_ids - existing)
        if unknown:
            raise HTTPException(422, f"briefing references unknown lead ids: {unknown}")
    return _upsert("briefing", "generated_at", payload)


@router.get("/insights")
def get_insights(date: str):
    return _fetch("insights", date)


@router.post("/insights")
def post_insights(payload: dict = Body(...)):
    return _upsert("insights", "computed_at", payload)


@router.get("/summary")
def get_summary(date: str):
    return _fetch("daily_summary", date)


@router.post("/summary")
def post_summary(body: DailySummaryPost):
    return _upsert(
        "daily_summary",
        "generated_at",
        body.model_dump(mode="json"),
    )
