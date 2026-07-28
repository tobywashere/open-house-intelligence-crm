"""Local calendar: availability + conflict detection in SQLite, .ics export.

This is the demo-safe adapter — works with zero internet. google_calendar.py can
replace it behind the same three functions if there's spare time at the end.
"""
from datetime import datetime, timedelta

SLOT_MINUTES = 45


def parse_ts(ts: str) -> datetime:
    """Parse a stored/incoming timestamp into NAIVE LOCAL wall-clock time —
    the one timestamp convention used at every API boundary in this system.

    Naive input is assumed to already be local and is returned as-is. Aware
    input (e.g. a `Z`-suffixed UTC timestamp from a browser or an external
    calendar) is CONVERTED to local time, then made naive — never simply
    stripped. Stripping would otherwise silently misinterpret the instant
    (e.g. treat 17:00 UTC as 17:00 local), which is exactly the bug that
    made dashboard-created reminders land in Google Calendar 7-8 hours off.
    A single mixed-convention row would also make later comparisons raise
    "can't compare offset-naive and offset-aware datetimes" and 500 all
    booking + availability calls."""
    dt = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def free_slots(conn, date_str: str) -> list[dict]:
    """Free SLOT_MINUTES slots on date_str (YYYY-MM-DD), conflicts removed."""
    day = parse_ts(date_str)
    weekday = day.weekday()
    windows = conn.execute(
        "SELECT start_time, end_time FROM availability WHERE weekday = ?", (weekday,)
    ).fetchall()

    day_start = f"{date_str}T00:00:00"
    day_end = f"{date_str}T23:59:59"
    # filter on end_ts too: an appointment starting the night before and
    # running past midnight still blocks this morning's slots
    booked = conn.execute(
        "SELECT start_ts, end_ts FROM appointments "
        "WHERE end_ts >= ? AND start_ts <= ?",
        (day_start, day_end),
    ).fetchall()

    slots = []
    for w in windows:
        cur = parse_ts(f"{date_str}T{w['start_time']}")
        end = parse_ts(f"{date_str}T{w['end_time']}")
        while cur + timedelta(minutes=SLOT_MINUTES) <= end:
            slot_end = cur + timedelta(minutes=SLOT_MINUTES)
            if not any(_overlaps(cur, slot_end, b["start_ts"], b["end_ts"]) for b in booked):
                slots.append({"start_ts": cur.isoformat(), "end_ts": slot_end.isoformat()})
            cur = slot_end
    return slots


def has_conflict(conn, start_ts: str, end_ts: str) -> bool:
    start = parse_ts(start_ts)
    end = parse_ts(end_ts)
    booked = conn.execute(
        "SELECT start_ts, end_ts FROM appointments"
    ).fetchall()
    return any(_overlaps(start, end, b["start_ts"], b["end_ts"]) for b in booked)


def to_ics(appointment: dict, lead_name: str) -> str:
    start = parse_ts(appointment["start_ts"]).strftime("%Y%m%dT%H%M%S")
    end = parse_ts(appointment["end_ts"]).strftime("%Y%m%dT%H%M%S")
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
    return start_a < parse_ts(end_b) and end_a > parse_ts(start_b)
