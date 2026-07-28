"""Vertical pack loading. A pack is verticals/<name>/pack.json; every key is
optional and merges over DEFAULT_PACK, so a missing/partial/broken pack degrades
to real-estate behavior instead of failing. Nothing here touches the DB."""
from __future__ import annotations

import json
import logging
import os
import re
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

# Merge semantics (fix round 1, Task 3b): most dict-valued keys (e.g. `labels`,
# `copy`) benefit from a *partial* override — a pack setting only
# labels.budget shouldn't have to restate area/timeline/intent too, and the
# existing tests rely on that. But a few keys are self-contained *content
# blocks* rather than a bag of independent label overrides, and a one-level
# dict.update() there silently leaks real-estate content into another
# vertical's pack:
#   - "mock_summary": a recruiting pack supplying its own {greeting,
#     ai_insights} would otherwise keep the real-estate `market_watch` array
#     (Issaquah/Freddie Mac news) verbatim, since it's a sibling key one
#     level down that the shallow merge never touches.
#   - "schedule_titles": a pack setting only "default" would keep
#     "sell": "Listing appointment" from the real-estate pack.
#   - "persona_recommendations": keyed by persona name; a pack defining its
#     own personas would otherwise inherit recommendation text addressed to
#     real-estate personas it doesn't have (e.g. "Ask about schools first"
#     for a recruiting pack that never defines "Growing Family").
# These keys replace wholesale instead: if present (and a truthy dict) in the
# pack, the pack's value is used as-is, not merged over the default's. Two
# behaviors worth knowing (fix round 2):
#   (a) An EMPTY dict for one of these keys keeps the DEFAULT_PACK value,
#       because the guard below is `isinstance(value, dict) and value` — a
#       pack cannot express "deliberately no schedule titles" this way. This
#       is a deliberate (if narrow) limitation, not a bug: the fallback-to-
#       default behavior is what keeps a broken/sparse pack demoable at all.
#   (b) A non-dict value (e.g. `"mock_summary": "nonsense"`) is rejected and
#       the default is kept — type-checked so a malformed wholesale value
#       can't reach the dashboard as-is (dashboard code does `.map()` etc.
#       assuming the documented shape).
REPLACE_WHOLESALE_KEYS = {"mock_summary", "schedule_titles", "persona_recommendations"}

# persona_rules `when` vocabulary (Task 3b) — see PersonaCond in
# dashboard/src/vertical.ts for the client-side type. Anything outside this
# is dropped by _sanitize_persona_rules() below, same spirit as
# _sanitize_stages() for KNOWN_RULE_TYPES: a pack is untrusted, third-party-
# authored JSON, and personaOf() (dashboard/src/briefing.ts) is called
# directly in render with no ErrorBoundary in the app — a malformed rule
# must never reach the client, so it's sanitized at the loader (fix round 2;
# fix round 1 added defense-in-depth guards in the TS evaluator itself, which
# stay in place for a dashboard that might be served by a backend predating
# this sanitizer).
#
# One vacuous-match note worth knowing: `{"all": []}` (every() over an empty
# list) matches unconditionally, same as JS's `Array.prototype.every`. A rule
# whose `all` list is emptied out by sanitization (every sub-condition was
# invalid) therefore becomes an unconditional match at whatever position it
# sits in `persona_rules` — it does not get dropped just because its
# sub-conditions did. `{"any": []}` is the opposite: `.some()` over an empty
# list is always false, so an emptied `any` never matches.
KNOWN_PERSONA_OPS = {"eq", "lt", "lte", "gt", "gte", "regex"}

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
        "insights.meeting_verb_phrase": "book a meeting",
        "insights.meeting_noun": "meeting",
        "insights.tour_noun": "tour",
        "insights.tour_noun_plural": "tours",
        "insights.demand_actor_plural": "active buyers",
        "funnel.action_book_tours_sub": "Only {n} upcoming — tours drive offers",
        "export.upcoming_tours_heading": "## Upcoming tours",
        "export.summary_title": "Home search summary",
        "note.offer_heading": "Log an offer",
        "note.offer_chip": "💰 Offer",
        "note.offer_saved": "Offer logged — it now counts in the funnel.",
        "note.offer_placeholder": 'e.g. "Offer submitted: $1,250,000 on the Lakemont house"',
        "note.note_placeholder": 'e.g. "Spoke on the phone — wants to see the Lakemont house this weekend"',
    },
    # App wordmark (Task 3b) — two-tone, e.g. "Open House" + "Intelligence".
    # Kept out of `copy` (not a copy() lookup site) since App.tsx renders the
    # two parts in separate spans (plain + `.brand-gradient`) and needs both
    # halves distinctly, not one interpolated string.
    "brand": {"name": "Open House", "name_accent": "Intelligence"},
    # Mock briefing generator (Task 3b) — declarative persona inference.
    # Ordered, first-match-wins, mirroring the original `if/else if` chain in
    # dashboard/src/briefing.ts exactly. `when` vocabulary: {field, op, value}
    # with op in eq/lt/lte/gt/gte/regex (+ optional `flags`), or a compound
    # {any: [...]} / {all: [...]} of sub-conditions. any/all were added beyond
    # the brief's minimum sketch because two of today's rules (Growing Family,
    # Relocating Professional) test an OR across two different lead fields —
    # {field,op,value} alone cannot express that, so the vocabulary was
    # widened rather than approximating those rules. The last rule has no
    # `when` — the unconditional default.
    "persona_rules": [
        {"persona": "Seller", "when": {"field": "intent", "op": "eq", "value": "sell"}},
        {"persona": "Luxury Executive", "when": {"field": "budget", "op": "gte", "value": 1_400_000}},
        {
            "persona": "Growing Family",
            "when": {
                "any": [
                    {"field": "preferences_text", "op": "regex", "value": "school|yard|family|cul-de-sac"},
                    {"field": "name", "op": "regex", "value": "&| and ", "flags": "i"},
                ]
            },
        },
        {
            "persona": "Relocating Professional",
            "when": {
                "any": [
                    {"field": "preferences_text", "op": "regex", "value": "relocat"},
                    {"field": "timeline", "op": "regex", "value": "week|asap", "flags": "i"},
                ]
            },
        },
        {
            "persona": "First-Time Buyer",
            "when": {"all": [{"field": "budget", "op": "gt", "value": 0}, {"field": "budget", "op": "lt", "value": 700_000}]},
        },
        {"persona": "Home Buyer", "when": None},
    ],
    # Keyed by persona; a persona absent here (e.g. "Home Buyer", "First-Time
    # Buyer" today) falls back to persona_recommendation_default, kept as a
    # separate key (not a map entry) so every key in this map is a real
    # persona name — see test_persona_rules_reproduce_the_shipped_inference.
    "persona_recommendations": {
        "Luxury Executive": "Lead with data — comps and market evidence, not opinions.",
        "Growing Family": "Ask about schools first; keep the shortlist to three homes.",
        "Relocating Professional": "Move fast — their {timeline} timeline is the priority.",
        "Seller": "Bring the listing presentation and a pricing range.",
    },
    "persona_recommendation_default": "Confirm their timeline and agree the next concrete step.",
    # Schedule block titles for the mock briefing, keyed by lead intent;
    # 'default' covers every intent without a more specific title (today only
    # 'sell' differs — matches the original ternary exactly).
    "schedule_titles": {"default": "Showing", "sell": "Listing appointment"},
    # Sample content for the daily-summary overlay's mock mode (Task 3b) —
    # moved out of dashboard/src/summary.ts verbatim so a non-real-estate pack
    # can supply its own sample market watch / AI insights narrative.
    "mock_summary": {
        "greeting": "Good morning, Annie — here is your day at a glance.",
        "market_watch": [
            {
                "title": "Issaquah leads state with program to self-certify backyard cottage plans",
                "source": "The Urbanist",
                "url": "https://www.theurbanist.org/issaquah-leads-state-with-program-to-self-certify-backyard-cottage-plans/",
                "date": "2026-07-24",
                "geo": "Eastside",
                "summary": "Issaquah is the first city in Washington to let homeowners self-certify DADU (backyard cottage) plans, changing permitting feasibility on every eligible lot — and a likely template for other Washington cities.",
                "takeaway": 'Every Issaquah homeowner you know just gained an option worth real money. Strong opener for owners on large lots weighing "improve vs. move."',
                "content_opportunity": "Client email to Issaquah homeowners: what to verify with the city before drawing backyard-cottage plans under the new self-certification program.",
            },
            {
                "title": "30-year fixed averages 6.58%, up from 6.55% last week",
                "source": "Freddie Mac PMMS",
                "url": "https://www.freddiemac.com/pmms",
                "date": "2026-07-23",
                "geo": "Washington State",
                "summary": "Both benchmarks ticked up this week — the 30-year to 6.58% and the 15-year to 5.96% — an actual printed move, distinct from commentary about expected cuts.",
                "takeaway": 'Rate-watching leads deciding between "lock now" and "wait" just saw the wait get slightly more expensive — a fact-based nudge, not a scare tactic.',
                "content_opportunity": 'Short post: "Rates moved up 3bps this week — what a $750K Eastside mortgage actually costs at 6.58% vs. 6.55%."',
            },
            {
                "title": "Bellevue moves to rezone Bellevue College campus to unlock expansion",
                "source": "The Registry Puget Sound",
                "url": "https://news.theregistryps.com/bellevue-moves-to-rezone-bellevue-college-campus-with-new-institutional-district-to-unlock-expansion/",
                "date": "2026-07-24",
                "geo": "Bellevue",
                "summary": "A proposed (not yet adopted) institutional zoning district would set the campus expansion envelope and reshape demand for adjacent Bellevue housing. The public comment window is the actionable moment.",
                "takeaway": "Buyers and owners near the campus should know this is proposed, not decided — being the agent who explains the process builds trust either way.",
                "content_opportunity": "Neighborhood newsletter section: what a proposed rezone means for adjacent streets, and exactly where in the process this one sits.",
            },
            {
                "title": "AT&T weighs exit from Bothell campus, eyes 250,000 sq ft in Bellevue",
                "source": "The Registry Puget Sound",
                "url": "https://news.theregistryps.com/att-weighs-exit-from-bothell-campus-eyes-250000-sqft-in-bellevue/",
                "date": "2026-07-24",
                "geo": "Eastside",
                "summary": "Under consideration only — no lease signed, no move decided. If it firms up, it is a two-submarket employment shift: Bothell vacancy against 250,000 sq ft of Bellevue absorption.",
                "takeaway": 'Do not present this to clients as decided. For anyone buying near the Bothell campus, "considering" is the operative word.',
                "content_opportunity": "Short-form video: why \"considering\" matters for anyone house-hunting near the Bothell campus right now.",
            },
        ],
        "ai_insights": [
            {
                "title": "Your referral channel is quietly your best",
                "body": "Referred leads move through your pipeline faster than any other source this month. Consider asking Linda Park and Priya Natarajan — both recently booked — for an introduction while the experience is fresh.",
            },
            {
                "title": "Evening momentum is real",
                "body": "Every tour on your calendar landed in an evening slot. When proposing times to new leads, offering 5–7pm first is likely to shorten the back-and-forth.",
            },
            {
                "title": "Two warm leads are one text from cold",
                "body": "Marcus Webb and Kevin O’Leary are both high-score and idle. A short, specific message today (new townhome listing for Marcus; Kirkland condo update for Kevin) keeps roughly $1.6M of pipeline moving.",
            },
        ],
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


def _sanitize_persona_cond(cond, rule_persona: str):
    """Validates one `when` condition (leaf or compound) for one persona
    rule. Returns the sanitized condition, or None if the condition itself
    is unusable (not a dict, unknown op, invalid regex, non-list any/all).
    Recurses into `any`/`all` so a single bad leaf deep in a tree is dropped
    without sinking sibling conditions in the same compound."""
    if not isinstance(cond, dict):
        logging.warning("vertical pack: dropping persona rule %r — condition %r is not an object",
                         rule_persona, cond)
        return None
    if "any" in cond or "all" in cond:
        sanitized = dict(cond)
        for combinator in ("any", "all"):
            if combinator not in cond:
                continue
            items = cond[combinator]
            if not isinstance(items, list):
                logging.warning(
                    "vertical pack: dropping persona rule %r — %r must be a list, got %r",
                    rule_persona, combinator, items)
                return None
            sanitized[combinator] = [
                c for c in (_sanitize_persona_cond(item, rule_persona) for item in items)
                if c is not None
            ]
        return sanitized
    if cond.get("op") not in KNOWN_PERSONA_OPS:
        logging.warning("vertical pack: dropping persona rule %r — unknown op %r",
                         rule_persona, cond.get("op"))
        return None
    if cond["op"] == "regex":
        try:
            re.compile(str(cond.get("value", "")))
        except re.error as exc:
            logging.warning("vertical pack: dropping persona rule %r — invalid regex %r (%s)",
                            rule_persona, cond.get("value"), exc)
            return None
    return cond


def _sanitize_persona_rules(rules) -> list:
    out = []
    for r in rules or []:
        if not isinstance(r, dict):
            logging.warning("vertical pack: dropping persona rule %r — not an object", r)
            continue
        persona = r.get("persona")
        if not isinstance(persona, str) or not persona:
            logging.warning("vertical pack: dropping persona rule with non-string persona %r", persona)
            continue
        when = r.get("when")
        if when is not None:
            sanitized_when = _sanitize_persona_cond(when, persona)
            if sanitized_when is None:
                # The top-level `when` itself was unusable (not a dict, or a
                # dict with an invalid op/regex/any/all). Dropping the whole
                # rule rather than silently promoting it to unconditional —
                # first-match-wins means an unconditional match here would
                # shadow every rule after it in the ordered list, which is a
                # much bigger behavior change than losing this one rule.
                continue
            when = sanitized_when
        out.append({"persona": persona, "when": when})
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
        elif key == "persona_rules":
            sanitized = _sanitize_persona_rules(value)
            if sanitized:
                pack["persona_rules"] = sanitized
            else:
                logging.warning(
                    "vertical pack: persona_rules had no valid rules after sanitizing — "
                    "keeping DEFAULT_PACK's persona_rules (an empty list would leave every "
                    "lead persona-less)")
        elif key in REPLACE_WHOLESALE_KEYS:
            # isinstance(..., dict) guards against e.g. `"mock_summary": "nonsense"`
            # reaching the dashboard verbatim — DailySummaryOverlay.tsx assumes the
            # documented shape and would throw on `.map()` over a string.
            if isinstance(value, dict) and value:
                pack[key] = value
        elif isinstance(value, dict) and isinstance(pack.get(key), dict):
            pack[key].update(value)
        elif value:
            pack[key] = value
    _cache = pack
    return pack
