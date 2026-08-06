"""Prepare-only local voice-note intake.

Audio is transcribed on the machine, converted into an editable CRM draft,
and deleted immediately. This endpoint never creates or changes a lead.
"""

import base64
import binascii
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, ValidationError

from ..agent import get_driver
from ..db import audit, get_conn
from ..duplicates import find_duplicate_candidates
from ..transcription import OpenClawTranscriber, TranscriptionError

router = APIRouter(prefix="/voice-note", tags=["voice"])

MAX_AUDIO_BYTES = 20 * 1024 * 1024
MAX_ENCODED_CHARS = ((MAX_AUDIO_BYTES + 2) // 3) * 4
TEMP_DIR = Path(tempfile.gettempdir()) / "open-intelligence-crm-voice"

KIND_TO_SUFFIX = {
    "webm": ".webm",
    "ogg": ".ogg",
    "wav": ".wav",
    "mp4": ".m4a",
    "mp3": ".mp3",
}
CONTENT_TYPE_KINDS = {
    "audio/webm": {"webm"},
    "video/webm": {"webm"},
    "audio/ogg": {"ogg"},
    "audio/wav": {"wav"},
    "audio/x-wav": {"wav"},
    "audio/mp4": {"mp4"},
    "audio/m4a": {"mp4"},
    "audio/x-m4a": {"mp4"},
    "audio/mpeg": {"mp3"},
    "audio/mp3": {"mp3"},
}


class VoicePrepareIn(BaseModel):
    filename: str
    content_type: str
    data: str


class VoiceDraft(BaseModel):
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    budget: int | None = Field(None, ge=0)
    area: str | None = None
    timeline: str | None = None
    intent: Literal["buy", "sell", "browse", "unknown"] = "unknown"
    preferences: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


def get_transcriber() -> OpenClawTranscriber:
    return OpenClawTranscriber()


def _sniff_audio_kind(audio: bytes) -> str | None:
    if audio.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if audio.startswith(b"OggS"):
        return "ogg"
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        return "wav"
    if len(audio) >= 12 and audio[4:8] == b"ftyp":
        return "mp4"
    if audio.startswith(b"ID3"):
        return "mp3"
    if len(audio) >= 2 and audio[0] == 0xFF and audio[1] & 0xE0 == 0xE0:
        return "mp3"
    return None


def _validate_audio(body: VoicePrepareIn) -> tuple[bytes, str]:
    if len(body.data) > MAX_ENCODED_CHARS:
        raise HTTPException(413, "Voice note is too large — maximum 20 MB.")
    try:
        audio = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Invalid voice-note payload.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Voice note is too large — maximum 20 MB.")

    kind = _sniff_audio_kind(audio)
    if kind is None:
        raise HTTPException(422, "The uploaded file is not recognized audio.")
    claimed = body.content_type.lower().split(";", 1)[0].strip()
    allowed_kinds = CONTENT_TYPE_KINDS.get(claimed)
    if not allowed_kinds:
        raise HTTPException(422, "The voice-note audio type is not supported.")
    if kind not in allowed_kinds:
        raise HTTPException(
            422, "The uploaded audio does not match its claimed content type."
        )
    return audio, kind


@router.post("/prepare")
async def prepare_voice_note(body: VoicePrepareIn):
    audio, kind = _validate_audio(body)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="voice-", suffix=KIND_TO_SUFFIX[kind], dir=TEMP_DIR, delete=False
        ) as handle:
            handle.write(audio)
            path = Path(handle.name)
        transcript = await run_in_threadpool(get_transcriber().transcribe, path)
    except TranscriptionError as exc:
        raise HTTPException(503, str(exc))
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    warnings: list[str] = []
    try:
        extracted = await get_driver().extract(transcript)
        fallback = extracted.pop("_fallback_used", None)
        draft = VoiceDraft.model_validate(extracted)
    except (ValidationError, TypeError, ValueError):
        raise HTTPException(
            502, "The local agent returned an invalid CRM draft. No lead was changed."
        )

    if fallback:
        warnings.append(
            "OpenClaw extraction was unavailable; this draft used the deterministic parser. Review every field."
        )
    if not draft.name:
        warnings.append("No name was detected. Add one before saving a new lead.")
    with get_conn() as conn:
        duplicates = find_duplicate_candidates(conn, draft.model_dump())
        audit(
            conn,
            "agent",
            "prepare_voice_note",
            {
                "audio_kind": kind,
                "audio_bytes": len(audio),
            },
            {
                "draft_fields": sorted(
                    key
                    for key, value in draft.model_dump().items()
                    if value not in (None, [], "")
                ),
                "duplicate_count": len(duplicates),
                "warning_count": len(warnings),
            },
        )

    return {
        "transcript": transcript,
        "draft": draft.model_dump(),
        "duplicates": duplicates,
        "warnings": warnings,
    }
