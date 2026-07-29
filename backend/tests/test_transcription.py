"""Local OpenClaw batch-transcription adapter."""

import subprocess

import pytest

from app.transcription import OpenClawTranscriber, TranscriptionError


def completed(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr="provider detail must stay private",
    )


def test_transcriber_uses_argument_vector_without_shell(monkeypatch, tmp_path):
    audio = tmp_path / "note.webm"
    audio.write_bytes(b"audio")
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return completed('{"text":"Met Taylor Brooks"}')

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert OpenClawTranscriber().transcribe(audio) == "Met Taylor Brooks"
    assert calls[0][0][:5] == [
        "openclaw",
        "infer",
        "audio",
        "transcribe",
        "--file",
    ]
    assert calls[0][0][5] == str(audio)
    assert calls[0][0][-1] == "--json"
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["stdin"] is subprocess.DEVNULL


def test_transcriber_passes_explicit_local_model(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("VOICE_TRANSCRIBE_MODEL", "local/whisper-base")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: calls.append(argv) or completed('{"transcript":"hello"}'),
    )

    assert OpenClawTranscriber().transcribe(tmp_path / "note.wav") == "hello"
    assert calls[0][-3:] == ["--model", "local/whisper-base", "--json"]


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "not json",
        '{"text":""}',
        '{"data":{}}',
    ],
)
def test_transcriber_rejects_missing_transcript(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kwargs: completed(payload),
    )

    with pytest.raises(TranscriptionError, match="structured transcript"):
        OpenClawTranscriber().transcribe(tmp_path / "note.webm")


def test_transcriber_maps_timeout_to_sanitized_error(monkeypatch, tmp_path):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["openclaw"], timeout=45)

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(TranscriptionError, match="timed out"):
        OpenClawTranscriber().transcribe(tmp_path / "note.webm")
