"""Local calendar: availability + conflict detection in SQLite, .ics export.

This is the demo-safe adapter — works with zero internet. google_calendar.py can
replace it behind the same three functions if there's spare time at the end.
"""
from datetime import datetime, timedelta

SLOT_MINUTES = 45


def free_slots(conn, date_str: str) -> list[dict]:
    """Free SLOT_MINUTES slots on date_str (YYYY-MM-DD), conflicts removed."""
    day = datetime.fromisoformat(date_str)
    weekday = day.weekday()
    windows = conn.execute(
        "SELECT start_time, end_time FROM availability WHERE weekday = ?", (weekday,)
    ).fetchall()

    day_start = f"{date_str}T00:00:00"
    day_end = f"{date_str}T23:59:59"
    booked = conn.execute(
        "SELECT start_ts, end_ts FROM appointments "
        "WHERE start_ts >= ? AND start_ts <= ?",
        (day_start, day_end),
    ).fetchall()

    slots = []
    for w in windows:
        cur = datetime.fromisoformat(f"{date_str}T{w['start_time']}")
        end = datetime.fromisoformat(f"{date_str}T{w['end_time']}")
        while cur + timedelta(minutes=SLOT_MINUTES) <= end:
            slot_end = cur + timedelta(minutes=SLOT_MINUTES)
            if not any(_overlaps(cur, slot_end, b["start_ts"], b["end_ts"]) for b in booked):
                slots.append({"start_ts": cur.isoformat(), "end_ts": slot_end.isoformat()})
            cur = slot_end
    return slots


def has_conflict(conn, start_ts: str, end_ts: str) -> bool:
    start = datetime.fromisoformat(start_ts)
    end = datetime.fromisoformat(end_ts)
    booked = conn.execute(
        "SELECT start_ts, end_ts FROM appointments"
    ).fetchall()
    return any(_overlaps(start, end, b["start_ts"], b["end_ts"]) for b in booked)


def to_ics(appointment: dict, lead_name: str) -> str:
    start = datetime.fromisoformat(appointment["start_ts"]).strftime("%Y%m%dT%H%M%S")
    end = datetime.fromisoformat(appointment["end_ts"]).strftime("%Y%m%dT%H%M%S")
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OpenHouseIntelligence//EN",
        "BEGIN:VEVENT",
        f"UID:ohi-appt-{appointment['id']}@openhouse.local",
        f"DTSTART:{start}",
        f"DTEND:{end}",
        f"SUMMARY:Home tour with {lead_name}",
        f"LOCATION:{appointment.get('location') or 'TBD'}",
        "END:VEVENT",
        "END:VCALENDAR",
    ])


def _overlaps(start_a: datetime, end_a: datetime, start_b: str, end_b: str) -> bool:
    return start_a < datetime.fromisoformat(end_b) and end_a > datetime.fromisoformat(start_b)
