"""Local batch audio transcription through the OpenClaw CLI."""

import json
import os
import subprocess
from pathlib import Path


class TranscriptionError(Exception):
    pass


class OpenClawTranscriber:
    def transcribe(self, path: Path) -> str:
        command = os.environ.get("VOICE_TRANSCRIBE_COMMAND", "openclaw")
        model = os.environ.get("VOICE_TRANSCRIBE_MODEL", "").strip()
        timeout = float(os.environ.get("VOICE_TRANSCRIBE_TIMEOUT_SECONDS", "120"))

        argv = [
            command,
            "infer",
            "audio",
            "transcribe",
            "--file",
            str(path),
        ]
        if model:
            argv.extend(["--model", model])
        argv.append("--json")

        try:
            result = subprocess.run(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise TranscriptionError("Local transcription timed out.") from exc
        except FileNotFoundError as exc:
            raise TranscriptionError(
                "OpenClaw transcription command was not found."
            ) from exc

        if result.returncode != 0:
            raise TranscriptionError(
                "Local transcription command failed; check OpenClaw logs."
            )
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TranscriptionError(
                "OpenClaw did not return a structured transcript."
            ) from exc

        transcript = payload.get("text") or payload.get("transcript")
        if not transcript and isinstance(payload.get("data"), dict):
            transcript = (
                payload["data"].get("text")
                or payload["data"].get("transcript")
            )
        if not isinstance(transcript, str) or not transcript.strip():
            raise TranscriptionError(
                "OpenClaw did not return a structured transcript."
            )
        return transcript.strip()
