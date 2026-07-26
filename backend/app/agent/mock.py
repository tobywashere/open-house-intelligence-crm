"""Deterministic stand-in for the real agent so dashboard/backend dev never blocks on the GB10.

Toby: the real behavior replaces this behind the same AgentDriver interface —
endpoints, response shapes, and the dashboard don't change.
"""
import re

from .base import AgentDriver

AREAS = ["Bellevue", "Redmond", "Kirkland", "Seattle", "Issaquah", "Sammamish", "Renton"]


class MockDriver(AgentDriver):
    name = "mock"

    async def chat(self, message: str, session_id: str) -> str:
        m = message.lower()
        if "neglect" in m or "follow up" in m:
            return ("[mock] I checked the pipeline — 2 leads haven't been touched in "
                    "3+ days. Top one: Marcus Webb (score 74). Want me to draft a follow-up?")
        if "book" in m or "appointment" in m or "meeting" in m:
            return ("[mock] Tuesday 6:00–6:45pm is free. I can book it and mark the "
                    "lead as meeting_booked — confirm?")
        if "sarah" in m:
            return ("[mock] Sarah Chen: Bellevue buyer, $1.1M budget, relocating on a "
                    "6-week timeline, husband wants to tour. Score 92 — highest priority "
                    "in the pipeline. Draft follow-up is ready on her profile.")
        return ("[mock] I'm the placeholder agent (AGENT_MODE=mock). On the GB10 this "
                "reply comes from the local model via OpenClaw. Ask about Sarah, "
                "booking, or neglected leads for canned demo replies.")

    async def extract(self, raw_text: str) -> dict:
        text = raw_text
        out: dict = {"preferences": [], "missing_fields": [], "intent": "unknown"}

        money = re.findall(r"\$?([\d,.]+)\s*(m|million|k)?", text, re.I)
        for num, suffix in money:
            try:
                val = float(num.replace(",", ""))
            except ValueError:
                continue
            suffix = (suffix or "").lower()
            if suffix in ("m", "million"):
                val *= 1_000_000
            elif suffix == "k":
                val *= 1_000
            if val >= 50_000:
                out["budget"] = int(val)
                break

        tl = re.search(r"(\d+\s*(?:week|month|day)s?)", text, re.I)
        if tl:
            out["timeline"] = tl.group(1)
        elif re.search(r"asap|immediately|urgent", text, re.I):
            out["timeline"] = "ASAP"

        for area in AREAS:
            if area.lower() in text.lower():
                out["area"] = area
                break

        if re.search(r"sell|listing|list my", text, re.I):
            out["intent"] = "sell"
        elif re.search(r"buy|tour|looking for|budget|relocat", text, re.I):
            out["intent"] = "buy"
        elif re.search(r"brows|curious|just looking", text, re.I):
            out["intent"] = "browse"

        phone = re.search(r"(\+?1?[\s.-]?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4})", text)
        if phone:
            out["phone"] = re.sub(r"[^\d+]", "", phone.group(1))
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        if email:
            out["email"] = email.group(0)

        for field in ("budget", "timeline", "area", "phone", "email"):
            if field not in out:
                out["missing_fields"].append(field)
        return out

    async def draft_followup(self, lead: dict) -> str:
        name = (lead.get("name") or "there").split()[0]
        area = lead.get("area") or "the area"
        bits = []
        if lead.get("budget"):
            bits.append(f"homes around ${lead['budget']:,.0f}")
        if lead.get("timeline"):
            bits.append(f"your {lead['timeline']} timeline")
        detail = " that fit " + " and ".join(bits) if bits else ""
        return (f"Hi {name}, great meeting you! I've pulled together a few listings in "
                f"{area}{detail}. Would Tuesday evening work for a quick tour? — [mock draft]")

    async def explain_score(self, lead: dict, score: int) -> str:
        reasons = []
        if lead.get("budget"):
            reasons.append("confirmed budget")
        if lead.get("timeline"):
            reasons.append(f"urgent timeline ({lead['timeline']})")
        if lead.get("intent") == "buy":
            reasons.append("clear buying intent")
        return f"Scored {score}: " + (", ".join(reasons) if reasons else "limited information so far") + ". [mock]"
