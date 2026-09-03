"""Authoritative runtime contract for the canonical CRM briefing response.

This module intentionally uses only the Python standard library so the same
contract can be reused by the backend and the standalone acceptance command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as Date


CANONICAL_BRIEFING_KEYS = frozenset(
    {
        "date",
        "generated_at",
        "source",
        "greeting",
        "schedule",
        "meeting_briefs",
        "suggested_actions",
    }
)
_SCHEDULE_KEYS = frozenset(
    {"appointment_id", "start", "end", "kind", "title", "lead_id"}
)
_MEETING_BRIEF_KEYS = frozenset(
    {
        "appointment_id",
        "lead_id",
        "name",
        "area",
        "budget",
        "timeline",
        "intent",
        "preferences",
        "persona",
        "score",
        "summary",
        "assistant_advice",
    }
)
_SUGGESTED_ACTION_KEYS = frozenset(
    {"lead_id", "name", "channel", "action", "reason", "evidence"}
)
_TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_GENERATED_AT_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
)


@dataclass(frozen=True)
class BriefingContractInspection:
    valid: bool
    date_matches: bool
    nested_shape_valid: bool
    unexpected_fields: tuple[str, ...]


def _strict_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _positive_int(value: object) -> bool:
    return _strict_int(value) and value > 0


def _text(value: object, *, optional: bool = False, empty: bool = False) -> bool:
    if optional and value is None:
        return True
    return isinstance(value, str) and (empty or bool(value.strip()))


def _schedule_item(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _SCHEDULE_KEYS
        and _positive_int(value.get("appointment_id"))
        and _positive_int(value.get("lead_id"))
        and isinstance(value.get("start"), str)
        and _TIME_RE.fullmatch(value["start"]) is not None
        and isinstance(value.get("end"), str)
        and _TIME_RE.fullmatch(value["end"]) is not None
        and value.get("kind") == "meeting"
        and _text(value.get("title"))
    )


def _assistant_advice(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != {"prepare", "recommendation"}:
        return False
    prepare = value.get("prepare")
    recommendation = value.get("recommendation")
    return (
        isinstance(prepare, list)
        and len(prepare) <= 10
        and all(_text(item, empty=True) for item in prepare)
        and _text(recommendation, optional=True, empty=True)
        and (recommendation is None or len(recommendation) <= 2000)
    )


def _meeting_brief(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _MEETING_BRIEF_KEYS:
        return False
    preferences = value.get("preferences")
    budget = value.get("budget")
    score = value.get("score")
    return (
        _positive_int(value.get("appointment_id"))
        and _positive_int(value.get("lead_id"))
        and _text(value.get("name"), empty=True)
        and _text(value.get("area"), optional=True, empty=True)
        and (budget is None or _strict_int(budget))
        and _text(value.get("timeline"), optional=True, empty=True)
        and _text(value.get("intent"), optional=True, empty=True)
        and isinstance(preferences, list)
        and all(_text(item, empty=True) for item in preferences)
        and _text(value.get("persona"), optional=True, empty=True)
        and (score is None or (_strict_int(score) and 0 <= score <= 100))
        and _text(value.get("summary"))
        and _assistant_advice(value.get("assistant_advice"))
    )


def _suggested_action(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _SUGGESTED_ACTION_KEYS:
        return False
    evidence = value.get("evidence")
    return (
        _positive_int(value.get("lead_id"))
        and _text(value.get("name"), empty=True)
        and value.get("channel") in {"email", "call", "text"}
        and _text(value.get("action"))
        and _text(value.get("reason"), empty=True)
        and isinstance(evidence, dict)
        and set(evidence) == {"kind", "id"}
        and evidence.get("kind") in {"reminder", "lead"}
        and _positive_int(evidence.get("id"))
    )


def inspect_briefing_response(
    value: object, expected_date: str
) -> BriefingContractInspection:
    """Inspect one decoded GET /briefing response without coercing values."""
    if not isinstance(value, dict):
        return BriefingContractInspection(False, False, False, ())

    unexpected = tuple(sorted(str(key) for key in set(value) - CANONICAL_BRIEFING_KEYS))
    date_matches = value.get("date") == expected_date
    try:
        valid_expected_date = Date.fromisoformat(expected_date).isoformat() == expected_date
    except (TypeError, ValueError):
        valid_expected_date = False

    schedule = value.get("schedule")
    meeting_briefs = value.get("meeting_briefs")
    actions = value.get("suggested_actions")
    nested_shape_valid = (
        isinstance(schedule, list)
        and all(_schedule_item(item) for item in schedule)
        and isinstance(meeting_briefs, list)
        and all(_meeting_brief(item) for item in meeting_briefs)
        and isinstance(actions, list)
        and all(_suggested_action(item) for item in actions)
    )
    top_level_valid = (
        set(value) == CANONICAL_BRIEFING_KEYS
        and date_matches
        and valid_expected_date
        and isinstance(value.get("generated_at"), str)
        and _GENERATED_AT_RE.fullmatch(value["generated_at"]) is not None
        and value.get("source") == "crm"
        and _text(value.get("greeting"))
    )
    return BriefingContractInspection(
        valid=top_level_valid and nested_shape_valid,
        date_matches=date_matches,
        nested_shape_valid=nested_shape_valid,
        unexpected_fields=unexpected,
    )


def require_briefing_response(value: dict, expected_date: str) -> dict:
    """Return a valid canonical response or fail closed at the backend boundary."""
    if not inspect_briefing_response(value, expected_date).valid:
        raise ValueError("generated briefing did not match the canonical response contract")
    return value
