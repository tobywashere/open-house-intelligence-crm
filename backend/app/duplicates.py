"""Shared, side-effect-free duplicate candidate matching."""

import difflib
import re
import sqlite3

from .db import row_to_dict

PLACEHOLDER_NAME = "Unknown lead"


def _phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    # The CRM currently targets North American agents. Treat a leading US/
    # Canada country code as optional so "(425)..." and "+1 425..." match.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def _email(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized or None


def find_duplicate_candidates(
    conn: sqlite3.Connection,
    fields: dict,
    *,
    exclude_lead_id: int | None = None,
) -> list[dict]:
    """Return existing leads that likely represent ``fields``.

    Exact normalized phone and email matches take precedence over fuzzy name
    matching. The shared unknown-name placeholder is never treated as a real
    name, so unrelated incomplete records do not match each other.
    """
    clauses: list[str] = []
    params: list[int] = []
    if exclude_lead_id is not None:
        clauses.append("id != ?")
        params.append(exclude_lead_id)
    query = "SELECT * FROM leads"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    phone = _phone(fields.get("phone"))
    email = _email(fields.get("email"))
    name = (fields.get("name") or "").strip()
    matches: list[dict] = []

    for row in conn.execute(query, params):
        lead = row_to_dict(row)
        match_on: str | None = None
        if phone and _phone(lead.get("phone")) == phone:
            match_on = "phone"
        elif email and _email(lead.get("email")) == email:
            match_on = "email"
        elif (
            name
            and name != PLACEHOLDER_NAME
            and lead.get("name")
            and lead["name"] != PLACEHOLDER_NAME
            and difflib.SequenceMatcher(
                None, name.lower(), lead["name"].lower()
            ).ratio()
            > 0.85
        ):
            match_on = "name"
        if match_on:
            matches.append({"lead": lead, "match_on": match_on})
    return matches
