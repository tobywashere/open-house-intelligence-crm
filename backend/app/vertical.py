"""Vertical pack loading. A pack is verticals/<name>/pack.json; every key is
optional and merges over DEFAULT_PACK, so a missing/partial/broken pack degrades
to real-estate behavior instead of failing. Nothing here touches the DB."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Stage rule vocabulary — anything else is dropped by _sanitize_stages():
#   {"type": "all"}                              every lead
#   {"type": "status_at_least", "status": "..."} status rank >= that status
#   {"type": "status_at_least_or_score", "status": "...", "score_status": "...", "min_score": 70}
#     auto-qualifies at rank >= "status", OR at rank >= "score_status" with score >= min_score.
#     Both thresholds are explicit — no implicit rank offset between them.
#   {"type": "event_type_or_status", "event_type": "offer", "status": "closed"}
#   {"type": "status_is", "status": "closed"}
KNOWN_RULE_TYPES = {"all", "status_at_least", "status_at_least_or_score",
                    "event_type_or_status", "status_is"}

DEFAULT_PACK: dict = {
    "name": "real-estate",
    "display_name": "Real estate",
    "stages": [
        {"key": "new", "label": "New leads", "rule": {"type": "all"}},
        {"key": "contacted", "label": "Contacted",
         "rule": {"type": "status_at_least", "status": "contacted"}},
        {"key": "qualified", "label": "Qualified",
         "rule": {"type": "status_at_least_or_score", "status": "meeting_booked",
                  "score_status": "contacted", "min_score": 70}},
        {"key": "tours", "label": "Tours booked",
         "rule": {"type": "status_at_least", "status": "meeting_booked"}},
        {"key": "offers", "label": "Offers submitted",
         "rule": {"type": "event_type_or_status", "event_type": "offer", "status": "closed"}},
        {"key": "closed", "label": "Closed",
         "rule": {"type": "status_is", "status": "closed"}},
    ],
    "labels": {"budget": "Budget", "area": "Area", "timeline": "Timeline",
               "intent": "Intent"},
    "intent_values": [
        {"value": "buy", "label": "Buy"},
        {"value": "sell", "label": "Sell"},
        {"value": "browse", "label": "Browse"},
        {"value": "unknown", "label": "Unknown"},
    ],
    "personas": [
        {"key": "Luxury Executive", "default": False},
        {"key": "Growing Family", "default": False},
        {"key": "Relocating Professional", "default": False},
        {"key": "First-Time Buyer", "default": False},
        {"key": "Seller", "default": False},
        {"key": "Home Buyer", "default": True},
    ],
    # Filled in by Task 3 (UI copy extraction). Start with representative
    # keys only — enumerating all strings is Task 3's job, not this loader's.
    "copy": {
        "app_name": "Open Intelligence CRM",
        "booking.booked": "Tour booked",
        "booking.cta": "Book a tour",
        "chat.example_1": "Add Minh Nguyen, 425-555-0198, buyer interested in Kirkland and Redmond",
        "chat.example_2": "Which active buyers need a follow-up?",
        "chat.example_3": "Show me everything we know about Sarah",
        "lead.subject_with_area": "Your home search in {area}",
        "lead.subject_generic": "Following up on your home search",
        "inbox.add_placeholder": 'New lead from a note, e.g. "Met Alex at the open house, looking in Redmond around $950k…"',
        "funnel.stage_negotiating": "In negotiation",
        "funnel.action_book_tours_title": "Book more tours this week",
        "funnel.action_advance_title": "Advance {n} qualified lead{s} to a tour",
        "funnel.kpi_qualified_buyers": "Qualified buyers",
        "funnel.kpi_tours_scheduled": "Tours scheduled",
        "funnel.action_book_tours_sub": "Only {n} upcoming — tours drive offers",
        "export.upcoming_tours_heading": "## Upcoming tours",
        "export.summary_title": "Home search summary",
        "note.offer_heading": "Log an offer",
        "note.offer_chip": "💰 Offer",
        "note.offer_saved": "Offer logged — it now counts in the funnel.",
        "note.offer_placeholder": 'e.g. "Offer submitted: $1,250,000 on the Lakemont house"',
        "note.note_placeholder": 'e.g. "Spoke on the phone — wants to see the Lakemont house this weekend"',
    },
    # Filled in by Task 5 from prompts/seattle-real-estate-news-reporter.md.
    # Present now so the schema shape is stable for downstream consumers.
    "research": {},
}

_cache: dict | None = None


def clear_cache() -> None:
    global _cache
    _cache = None


def _verticals_dir() -> Path:
    return Path(os.environ.get("VERTICALS_DIR", REPO_ROOT / "verticals"))


def _sanitize_stages(stages) -> list:
    out = []
    for s in stages or []:
        if not isinstance(s, dict) or "key" not in s:
            continue
        rule = s.get("rule") or {}
        if rule.get("type") not in KNOWN_RULE_TYPES:
            logging.warning("vertical pack: dropping stage %r with unknown rule %r",
                            s.get("key"), rule.get("type"))
            continue
        out.append(s)
    return out


def load_pack() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    pack = json.loads(json.dumps(DEFAULT_PACK))   # deep copy
    path = _verticals_dir() / os.environ.get("VERTICAL", "real-estate") / "pack.json"
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        raw = {}
    except (json.JSONDecodeError, OSError) as exc:
        logging.warning("vertical pack at %s unreadable (%s) — using defaults", path, exc)
        raw = {}
    for key, value in raw.items():
        if key == "stages":
            sanitized = _sanitize_stages(value)
            if sanitized:
                pack["stages"] = sanitized
        elif isinstance(value, dict) and isinstance(pack.get(key), dict):
            pack[key].update(value)
        elif value:
            pack[key] = value
    _cache = pack
    return pack
