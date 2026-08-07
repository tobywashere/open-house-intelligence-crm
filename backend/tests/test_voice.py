"""Prepare-only voice-note intake API."""

import base64
from pathlib import Path

from app.db import get_conn
from app.routers import voice
from app.transcription import TranscriptionError


WAV_BYTES = b"RIFF" + (b"\x00" * 4) + b"WAVEfmt " + (b"\x00" * 32)


def voice_payload(audio: bytes = WAV_BYTES, **overrides) -> dict:
    payload = {
        "filename": "note.wav",
        "content_type": "audio/wav",
        "data": base64.b64encode(audio).decode(),
    }
    payload.update(overrides)
    return payload


def test_voice_prepare_rejects_non_audio_signature(client):
    response = client.post(
        "/api/voice-note/prepare",
        json={
            "filename": "note.webm",
            "content_type": "audio/webm",
            "data": base64.b64encode(b"<html>not audio</html>").decode(),
        },
    )

    assert response.status_code == 422
    assert "audio" in response.json()["detail"].lower()


def test_voice_prepare_rejects_malformed_base64(client):
    response = client.post(
        "/api/voice-note/prepare",
        json={
            "filename": "note.webm",
            "content_type": "audio/webm",
            "data": "%%not-base64%%",
        },
    )

    assert response.status_code == 400


def test_voice_prepare_transcribes_extracts_and_never_writes_lead(
    client, monkeypatch, tmp_path
):
    seen_path: Path | None = None

    class FakeTranscriber:
        def transcribe(self, path: Path) -> str:
            nonlocal seen_path
            seen_path = path
            assert path.read_bytes() == WAV_BYTES
            return (
                "Met Priya Shah. She wants to buy in Kirkland around $900k "
                "within 6 weeks. priya@example.com"
            )

    monkeypatch.setattr(voice, "get_transcriber", lambda: FakeTranscriber())
    monkeypatch.setattr(voice, "TEMP_DIR", tmp_path)

    before = client.get("/api/leads").json()
    response = client.post("/api/voice-note/prepare", json=voice_payload())

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["transcript"].startswith("Met Priya Shah")
    assert result["draft"]["name"] == "Priya Shah"
    assert result["draft"]["email"] == "priya@example.com"
    assert result["draft"]["area"] == "Kirkland"
    assert result["duplicates"] == []
    assert client.get("/api/leads").json() == before
    assert seen_path is not None
    assert not seen_path.exists()
    assert list(tmp_path.iterdir()) == []

    with get_conn() as conn:
        log = conn.execute(
            "SELECT input, output FROM audit_log WHERE tool = 'prepare_voice_note'"
        ).fetchone()
    assert log is not None
    details = log["input"] + log["output"]
    assert "Priya Shah" not in details
    assert "priya@example.com" not in details
    assert "transcript" not in details


def test_voice_prepare_deletes_temp_file_when_transcription_fails(
    client, monkeypatch, tmp_path
):
    seen_path: Path | None = None

    class FailingTranscriber:
        def transcribe(self, path: Path) -> str:
            nonlocal seen_path
            seen_path = path
            raise TranscriptionError("Local transcription timed out.")

    monkeypatch.setattr(voice, "get_transcriber", lambda: FailingTranscriber())
    monkeypatch.setattr(voice, "TEMP_DIR", tmp_path)

    response = client.post("/api/voice-note/prepare", json=voice_payload())

    assert response.status_code == 503
    assert response.json()["detail"] == "Local transcription timed out."
    assert seen_path is not None
    assert not seen_path.exists()
    assert list(tmp_path.iterdir()) == []
    assert client.get("/api/leads").json() == []


def test_voice_prepare_finds_existing_phone_duplicate(client, monkeypatch):
    created = client.post(
        "/api/leads",
        json={"name": "Priya Shah", "phone": "+14255550123"},
    ).json()

    class FakeTranscriber:
        def transcribe(self, path: Path) -> str:
            return "Priya's number is (425) 555-0123."

    class FakeDriver:
        async def extract(self, raw_text: str) -> dict:
            return {
                "name": "Priya Shah",
                "phone": "425-555-0123",
                "intent": "unknown",
                "preferences": [],
                "missing_fields": ["email"],
            }

    monkeypatch.setattr(voice, "get_transcriber", lambda: FakeTranscriber())
    monkeypatch.setattr(voice, "get_driver", lambda: FakeDriver())

    response = client.post("/api/voice-note/prepare", json=voice_payload())

    assert response.status_code == 200, response.text
    duplicate = response.json()["duplicates"][0]
    assert duplicate["lead"]["id"] == created["id"]
    assert duplicate["match_on"] == "phone"


def test_voice_prepare_labels_deterministic_extraction_without_leaking_marker(
    client, monkeypatch
):
    class FakeTranscriber:
        def transcribe(self, path: Path) -> str:
            return "Met Priya Shah at an open house."

    class FallbackDriver:
        async def extract(self, raw_text: str) -> dict:
            return {
                "name": "Priya Shah",
                "intent": "buy",
                "preferences": [],
                "missing_fields": ["email"],
                "_fallback_used": "deterministic_parser",
            }

    monkeypatch.setattr(voice, "get_transcriber", lambda: FakeTranscriber())
    monkeypatch.setattr(voice, "get_driver", lambda: FallbackDriver())

    response = client.post("/api/voice-note/prepare", json=voice_payload())

    assert response.status_code == 200, response.text
    result = response.json()
    assert "_fallback_used" not in result["draft"]
    assert any("deterministic parser" in warning.lower() for warning in result["warnings"])


def test_voice_prepare_rejects_mismatched_claimed_content_type(client):
    response = client.post(
        "/api/voice-note/prepare",
        json=voice_payload(content_type="audio/webm"),
    )

    assert response.status_code == 422
    assert "does not match" in response.json()["detail"].lower()


def test_voice_prepare_rejects_oversized_encoded_payload_before_decoding(client):
    response = client.post(
        "/api/voice-note/prepare",
        json={
            "filename": "note.wav",
            "content_type": "audio/wav",
            "data": "A" * 28_000_001,
        },
    )

    assert response.status_code == 413
