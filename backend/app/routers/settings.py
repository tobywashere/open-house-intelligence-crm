"""Operator settings. Today: the daily market-research scope.

The vertical pack ships defaults (`research` in verticals/<name>/pack.json);
this router lets the operator edit them at runtime without touching a file,
because the research keywords are the one part of a vertical that gets tuned
repeatedly once real results come back. A stored row wins over the pack.

The rendered prompt is returned alongside the fields so the dashboard can show
exactly what the agent will be asked — no hidden prompt construction.
"""
import json
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..db import audit, get_conn
from ..vertical import load_pack

router = APIRouter(tags=["settings"])

SETTINGS_KEY = "research"
TEMPLATE_PATH = (Path(__file__).resolve().parent.parent.parent.parent
                 / "prompts" / "market-news-reporter.md.template")


class ResearchSettings(BaseModel):
    role: str = Field(min_length=1, max_length=300)
    audience: str = Field(min_length=1, max_length=200)
    lookback_days: int = Field(7, ge=1, le=90)
    regions: list[str] = Field(min_length=1)
    topics: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    national_scope_note: str = ""


def _bullets(items: list[str]) -> str:
    return "\n".join(str(i) for i in items)


def render_research_prompt(settings: dict) -> str:
    """Fill the template from a settings dict. Plain string replacement — no
    template engine, keeping the no-new-dependencies rule. A missing template
    file degrades to an empty string rather than 500ing the settings endpoint."""
    try:
        template = TEMPLATE_PATH.read_text()
    except OSError:
        return ""
    filled = template
    for key, value in settings.items():
        token = "{" + key + "}"
        if isinstance(value, list):
            filled = filled.replace(token, _bullets(value))
        else:
            filled = filled.replace(token, str(value))
    return filled


def _stored() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT payload FROM settings WHERE key = ?", (SETTINGS_KEY,)
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def current_research_settings() -> dict:
    """Stored row if the operator has saved one, else the active pack's
    defaults. Used by this router and by anything composing the daily prompt."""
    return _stored() or dict(load_pack().get("research") or {})


@router.get("/research-settings")
def get_research_settings() -> dict:
    # Read-only: no audit() call. CONTRACT §3 states exactly two reads audit
    # (GET /availability, GET /leads/{id}/duplicates) and this is not one.
    settings = current_research_settings()
    return {**settings, "rendered_prompt": render_research_prompt(settings)}


@router.put("/research-settings")
def put_research_settings(body: ResearchSettings) -> dict:
    payload = body.model_dump()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, payload) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET payload = excluded.payload, "
            "updated_at = (strftime('%Y-%m-%dT%H:%M:%S','now','localtime'))",
            (SETTINGS_KEY, json.dumps(payload)),
        )
        audit(conn, "user", "update_research_settings", payload, {})
    return {**payload, "rendered_prompt": render_research_prompt(payload)}
