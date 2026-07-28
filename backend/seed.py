"""Seed ~15 realistic leads, availability windows, and the Sarah Chen demo setup.

Run: python backend/seed.py   (wipes and recreates the database)
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import DB_PATH, get_conn, init_db  # noqa: E402
from app.scoring import score_lead  # noqa: E402

random.seed(42)  # deterministic seed data — same demo every run

LEADS = [
    # (name, phone, email, source, status, budget, area, timeline, intent, prefs, days_ago)
    ("Sarah Chen", "+14255550142", None, "form", "new", 1_100_000, "Bellevue", None, "buy",
     ["single family"], 0),
    ("Sarah C.", "+14255550142", "sarahc.relo@gmail.com", "text", "new", None, None, None,
     "unknown", [], 0),
    ("Marcus Webb", "+12065550187", "mwebb@outlook.com", "form", "contacted", 850_000,
     "Redmond", "3 months", "buy", ["townhome", "near Microsoft"], 4),
    ("Priya Natarajan", "+14255550119", "priya.n@gmail.com", "referral", "contacted",
     1_400_000, "Kirkland", "6 months", "buy", ["waterfront view", "4br"], 1),
    ("Dan Kowalski", "+12065550163", None, "note", "new", 650_000, "Renton", None, "buy",
     ["fixer-upper ok"], 5),
    ("Emily & Josh Tran", "+14255550171", "tranfamily@yahoo.com", "form", "contacted",
     975_000, "Issaquah", "2 months", "buy", ["good schools", "yard"], 1),
    ("Robert Adeyemi", "+12065550134", "r.adeyemi@proton.me", "note", "new", None,
     "Seattle", None, "sell", ["selling Ballard craftsman"], 2),
    ("Linda Park", "+14255550156", "lindapark22@gmail.com", "text", "meeting_booked",
     1_250_000, "Sammamish", "2 months", "buy", ["new construction"], 0),
    ("Tom Grigsby", None, "tgrigsby@aol.com", "form", "new", 500_000, "Renton", None,
     "browse", [], 7),
    ("Aisha Mohammed", "+12065550148", "aisha.m@gmail.com", "referral", "contacted",
     1_800_000, "Bellevue", "1 month", "buy", ["west of 405", "modern"], 0),
    ("Kevin O'Leary", "+14255550129", None, "note", "new", 720_000, "Kirkland",
     "6 months", "buy", ["condo ok"], 3),
    ("Grace Liu", "+12065550192", "grace.liu@hotmail.com", "form", "contacted", 930_000,
     "Redmond", None, "buy", ["3br minimum"], 6),
    ("The Hendersons", "+14255550183", "hendersonfam@gmail.com", "form", "new", 1_050_000,
     "Issaquah", "4 months", "buy", ["cul-de-sac", "3-car garage"], 2),
    ("Miguel Santos", "+12065550175", "msantos.re@gmail.com", "text", "new", None, None,
     "ASAP", "sell", ["relocating for work"], 1),
    ("Janet Wu", "+14255550107", "janetwu@icloud.com", "note", "closed", 880_000,
     "Seattle", None, "buy", [], 20),
]

SARAH_EVENTS = [
    (1, "form", "Open house sign-in (Lakemont Blvd listing): Sarah Chen, looking in "
                "Bellevue, budget around $1.1M, phone +14255550142."),
    (2, "text", "Text from +14255550142: Hi! This is Sarah — my husband would love to "
                "come tour the place too. What times work? sarahc.relo@gmail.com"),
    (2, "note", "Agent note: Sarah mentioned they're relocating from Chicago and need "
                "to close within 6 weeks. Husband's schedule is tight — evenings best."),
]

AVAILABILITY = [(d, "17:00", "20:00") for d in range(5)] + [(5, "10:00", "16:00")]


def main():
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()
    with get_conn() as conn:
        for i, (name, phone, email, source, status, budget, area, timeline,
                intent, prefs, days_ago) in enumerate(LEADS, start=1):
            lead = {"budget": budget, "timeline": timeline, "intent": intent,
                    "phone": phone, "email": email}
            score = score_lead(lead, event_count=random.randint(1, 4))
            conn.execute(
                "INSERT INTO leads (id, name, phone, email, source, status, budget, area, "
                "timeline, intent, preferences, score, score_reason, created_at, last_activity_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,"
                "strftime('%Y-%m-%dT%H:%M:%S', datetime('now','localtime', ?)), "
                "strftime('%Y-%m-%dT%H:%M:%S', datetime('now','localtime', ?)))",
                (i, name, phone, email, source, status, budget, area, timeline, intent,
                 json.dumps(prefs), score,
                 f"Seeded score {score} from budget/timeline/intent signals.",
                 f"-{days_ago + 1} days", f"-{days_ago} days"),
            )
            conn.execute(
                "INSERT INTO events (lead_id, type, content, created_at) VALUES (?,?,?,"
                "strftime('%Y-%m-%dT%H:%M:%S', datetime('now','localtime', ?)))",
                (i, source if source in ("form", "text", "note") else "note",
                 f"Initial contact via {source}.", f"-{days_ago + 1} days"),
            )

        for lead_id, etype, content in SARAH_EVENTS:
            conn.execute(
                "INSERT INTO events (lead_id, type, content) VALUES (?,?,?)",
                (lead_id, etype, content))

        for weekday, start, end in AVAILABILITY:
            conn.execute(
                "INSERT INTO availability (weekday, start_time, end_time) VALUES (?,?,?)",
                (weekday, start, end))

        conn.execute(
            "INSERT INTO audit_log (actor, tool, input, output, lead_id) VALUES "
            "('cron', 'seed_database', '{}', ?, NULL)",
            (json.dumps({"leads": len(LEADS)}),))

    print(f"Seeded {len(LEADS)} leads → {DB_PATH}")
    print("Demo tips:")
    print("  • Leads #1 and #2 are Sarah Chen's un-merged fragments (same phone) — "
          "use the merge demo.")
    print("  • POST /api/demo/advance-time {\"days\": 3} flags the stale leads as neglected.")


if __name__ == "__main__":
    main()
