"""Input-validation cluster + ICS escaping + scan hardening (review I4,I8,I10,minors)."""
import base64

import pytest

from tests.conftest import make_lead


@pytest.fixture()
def lead(client):
    return make_lead(client)["id"]


def test_score_bounds(client, lead):
    assert client.patch(f"/api/leads/{lead}", json={"score": 99999}).status_code == 422
    assert client.patch(f"/api/leads/{lead}", json={"is_neglected": 7}).status_code == 422


def test_limits_bounded(client):
    assert client.get("/api/audit?limit=-1").status_code == 422
    assert client.get("/api/chat/history?session_id=x&limit=99999999").status_code == 422


def test_advance_time_negative_400(client):
    assert client.post("/api/demo/advance-time", json={"days": -5}).status_code == 422


def test_scan_rejects_non_image(client):
    r = client.post("/api/scan-card", json={
        "data": base64.b64encode(b"<html>pwn</html>").decode(),
        "filename": "pwn.html"})
    assert r.status_code == 422


def test_scan_rejects_extension_content_mismatch(client):
    # .jpg extension but not actually JPEG magic bytes
    r = client.post("/api/scan-card", json={
        "data": base64.b64encode(b"not a real jpeg").decode(),
        "filename": "pwn.jpg"})
    assert r.status_code == 422


def test_scan_response_omits_absolute_path(client):
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    r = client.post("/api/scan-card", json={
        "data": base64.b64encode(jpeg_bytes).decode(),
        "filename": "card.jpg"})
    assert r.status_code == 200
    body = r.json()
    assert "image" not in body or "/" not in str(body.get("image", ""))


def test_ics_escapes_injection():
    # NOTE: the brief's literal `ics.count("BEGIN:VEVENT") == 1` is
    # unsatisfiable by any correct RFC-5545 escaper — escaping never removes
    # the *letters* "BEGIN:VEVENT" from injected text, it only stops a real
    # newline from turning it into a structural line. Verified by execution
    # (see task-8-report.md): with proper escaping the raw substring count is
    # 2 (the real event + the now-inert text inside SUMMARY), which is
    # exactly the safe outcome. The real security property — no *second ICS
    # line* named BEGIN:VEVENT — is checked below instead.
    from app.calendar_adapter.local_calendar import to_ics
    ics = to_ics({"id": 1, "start_ts": "2026-08-03T18:00:00",
                  "end_ts": "2026-08-03T18:45:00", "location": "A;B"},
                 "Eve\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:Injected")
    # splitlines() (universal newlines) — not split("\r\n") — because a lone
    # "\n" smuggled in unescaped is still a real line break to most parsers
    # and text viewers, even though it doesn't match a literal "\r\n".
    lines = ics.splitlines()
    assert lines.count("BEGIN:VEVENT") == 1
    assert lines.count("END:VEVENT") == 1
    assert "\\n" in ics and "A\\;B" in ics


def test_ics_escapes_lone_cr_injection():
    # A bare CR (no paired \n) is a raw line terminator to str.splitlines()
    # and to many real parsers/editors — RFC 5545 forbids raw CR in TEXT.
    # Falsified against the pre-fix _ics_escape (which only handled \r\n and
    # \n): that code left "Eve\rEND:VEVENT\rBEGIN:VEVENT\r..." unescaped,
    # scoring 2/2 BEGIN:VEVENT/END:VEVENT lines (the injection worked).
    from app.calendar_adapter.local_calendar import to_ics
    ics = to_ics({"id": 1, "start_ts": "2026-08-03T18:00:00",
                  "end_ts": "2026-08-03T18:45:00", "location": "A;B"},
                 "Eve\rEND:VEVENT\rBEGIN:VEVENT\rSUMMARY:Injected")
    lines = ics.splitlines()
    assert lines.count("BEGIN:VEVENT") == 1
    assert lines.count("END:VEVENT") == 1


def test_ics_escapes_crlf_as_single_newline():
    # A real CRLF pair in the input must collapse to exactly ONE escaped
    # "\n" — not two — otherwise \r\n would be double-escaped.
    from app.calendar_adapter.local_calendar import to_ics
    ics = to_ics({"id": 1, "start_ts": "2026-08-03T18:00:00",
                  "end_ts": "2026-08-03T18:45:00", "location": "TBD"},
                 "Eve\r\nBonus")
    summary_line = next(l for l in ics.split("\r\n") if l.startswith("SUMMARY:"))
    assert summary_line.count("\\n") == 1
    assert summary_line == "SUMMARY:Home tour with Eve\\nBonus"


def test_scan_extension_derived_from_sniffed_content(client):
    # JPEG bytes uploaded with a ".png" filename must be stored under the
    # extension the content sniff resolved to (.jpg), never the
    # client-claimed one — otherwise a client can pick an arbitrary
    # whitelisted extension independent of the real file type.
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 32
    r = client.post("/api/scan-card", json={
        "data": base64.b64encode(jpeg_bytes).decode(),
        "filename": "card.png"})
    assert r.status_code == 200
    from app.routers.scan import UPLOADS
    saved = sorted(UPLOADS.glob("card-*"))
    assert saved, "expected a saved upload"
    assert saved[-1].suffix == ".jpg"
