"""Deterministic lead scoring. K owns the weights; the LLM only writes score_reason."""


def score_lead(lead: dict, event_count: int = 0) -> int:
    score = 0

    # Budget known and substantial
    if lead.get("budget"):
        score += 25
        if lead["budget"] >= 750_000:
            score += 5

    # Timeline urgency
    timeline = (lead.get("timeline") or "").lower()
    if any(k in timeline for k in ("week", "asap", "immediately", "month")):
        score += 25
    elif timeline:
        score += 10

    # Intent
    intent = lead.get("intent") or "unknown"
    score += {"buy": 20, "sell": 20, "browse": 5}.get(intent, 0)

    # Contactability
    if lead.get("phone"):
        score += 5
    if lead.get("email"):
        score += 5

    # Engagement: more touchpoints = warmer
    score += min(event_count * 4, 15)

    return min(score, 100)


def is_high_priority(score: int | None) -> bool:
    return (score or 0) >= 70
