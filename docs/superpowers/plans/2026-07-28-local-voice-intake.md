# Local Voice-note Intake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record or upload a voice note, transcribe it locally through OpenClaw, review extracted CRM fields and duplicates, and save only after explicit confirmation.

**Architecture:** A transcription adapter invokes `openclaw infer audio transcribe` with an argument vector and a temporary file. A prepare-only API returns transcript, validated extracted fields, and duplicate candidates. The React workflow owns recording and confirmation; existing lead APIs perform the mutation.

**Tech Stack:** Python subprocess/tempfile, FastAPI, Pydantic, pytest, React MediaRecorder, TypeScript, Vite.

## Global Constraints

- Apple-silicon Mac mini with 16 GB RAM is the minimum supported host.
- No cloud fallback and no shell command interpolation.
- Maximum decoded audio size is 20 MB.
- Raw audio is deleted after every request and never placed in the audit log.
- Transcription/extraction never creates or updates a lead.

---

### Task 1: Local transcription adapter

**Files:**
- Create: `backend/app/transcription.py`
- Create: `backend/tests/test_transcription.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `TranscriptionError`, `OpenClawTranscriber.transcribe(path: Path) -> str`.
- Reads: `VOICE_TRANSCRIBE_COMMAND`, `VOICE_TRANSCRIBE_MODEL`, `VOICE_TRANSCRIBE_TIMEOUT_SECONDS`.

- [ ] **Step 1: Write failing adapter tests**

```python
def test_transcriber_uses_argument_vector_without_shell(monkeypatch, tmp_path):
    audio = tmp_path / "note.webm"
    audio.write_bytes(b"audio")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append((argv, kw)) or completed(
        stdout='{"text":"Met Taylor Brooks"}'
    ))
    assert OpenClawTranscriber().transcribe(audio) == "Met Taylor Brooks"
    assert calls[0][0][:5] == ["openclaw", "infer", "audio", "transcribe", "--file"]
    assert calls[0][0][5] == str(audio)
    assert calls[0][1]["shell"] is False


@pytest.mark.parametrize("payload", ["{}", "not json", '{"text":""}'])
def test_transcriber_rejects_missing_transcript(monkeypatch, tmp_path, payload):
    install_completed(monkeypatch, stdout=payload)
    with pytest.raises(TranscriptionError):
        OpenClawTranscriber().transcribe(tmp_path / "note.webm")
```

- [ ] **Step 2: Run and verify the missing-module failure**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_transcription.py -q
```

- [ ] **Step 3: Implement the adapter**

Build:

```python
argv = [command, "infer", "audio", "transcribe", "--file", str(path), "--json"]
if model:
    argv.extend(["--model", model])
```

Call `subprocess.run(argv, shell=False, stdin=DEVNULL, capture_output=True,
text=True, timeout=timeout)`. Parse `text`, `transcript`, or `data.text`; reject
empty output, nonzero exit, malformed JSON, and timeout with sanitized messages.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_transcription.py -q
git add backend/app/transcription.py backend/tests/test_transcription.py .env.example
git commit -m "feat: add local OpenClaw transcription adapter"
```

### Task 2: Prepare-only voice API

**Files:**
- Create: `backend/app/routers/voice.py`
- Create: `backend/app/duplicates.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/routers/leads.py`
- Modify: `backend/app/routers/scan.py`
- Create: `backend/tests/test_voice.py`

**Interfaces:**
- Adds: `POST /api/voice-note/prepare`.
- Produces: `{transcript, draft, duplicates, warnings}`.
- Produces: `find_duplicate_candidates(conn, fields: Mapping[str, Any]) -> list[dict]`.

- [ ] **Step 1: Write failing validation and zero-write tests**

```python
def test_voice_prepare_rejects_non_audio(client):
    response = client.post("/api/voice-note/prepare", json={
        "filename": "note.webm",
        "content_type": "audio/webm",
        "data": base64.b64encode(b"<html>").decode(),
    })
    assert response.status_code == 422


def test_voice_prepare_returns_review_without_creating_lead(client, monkeypatch):
    monkeypatch.setattr(voice, "get_transcriber", lambda: FakeTranscriber(
        "Met Taylor Brooks, buyer in Bellevue around $900k"
    ))
    before = client.get("/api/leads").json()
    response = client.post("/api/voice-note/prepare", json=valid_webm_payload())
    assert response.status_code == 200
    assert response.json()["draft"]["name"] == "Taylor Brooks"
    assert client.get("/api/leads").json() == before
```

Add tests for malformed base64, 20 MB boundary, MIME/signature mismatch,
timeout mapping to 503, temp-file deletion on success/failure, and duplicate
phone/email/name results.

- [ ] **Step 2: Run and verify 404 failures**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_voice.py -q
```

- [ ] **Step 3: Implement audio validation**

Accept WebM/Matroska (`1a45dfa3`), Ogg (`OggS`), WAV (`RIFF....WAVE`),
MP4/M4A (`ftyp`), and MP3 (`ID3` or frame sync). Decode with
`base64.b64decode(data, validate=True)`. Reject decoded payloads above
20 MiB.

- [ ] **Step 4: Implement prepare-only orchestration**

Write to `NamedTemporaryFile(delete=False, suffix=sniffed_extension)`, call the
transcriber in `run_in_threadpool`, unlink in `finally`, call the existing
driver's `extract(transcript)`, validate fields through a `VoiceDraft` model,
query duplicate candidates, and audit only:

```json
{"filename":"note.webm","bytes":12345,"transcribed":true,"duplicate_count":0}
```

Never audit audio or transcript content.

- [ ] **Step 5: Share duplicate logic**

Move normalized phone/email/name matching into `duplicates.py`. Have lead
duplicate review, card scan, and voice preparation call the same function.
Keep the existing rule that two `"Unknown lead"` placeholders do not match.

- [ ] **Step 6: Run focused/full tests and commit**

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests/test_voice.py backend/tests/test_extract.py backend/tests/test_validation.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest backend/tests -p no:cacheprovider -q
git add backend/app/routers/voice.py backend/app/duplicates.py backend/app/main.py backend/app/routers/leads.py backend/app/routers/scan.py backend/tests/test_voice.py
git commit -m "feat: prepare voice notes without writing CRM data"
```

### Task 3: Browser record, review, and confirm flow

**Files:**
- Create: `dashboard/src/pages/VoiceNote.tsx`
- Modify: `dashboard/src/api.ts`
- Modify: `dashboard/src/App.tsx`
- Modify: `dashboard/src/pages/Dashboard.tsx`

**Interfaces:**
- Consumes: `api.prepareVoiceNote(filename, contentType, data)`.
- Uses: existing `api.createLeadFields`, `api.addEvent`, `api.processLead`.

- [ ] **Step 1: Add API types and intentionally break the build at the new route**

Define:

```typescript
export interface VoiceDraft {
  name?: string
  phone?: string
  email?: string
  budget?: number
  area?: string
  timeline?: string
  intent?: string
  preferences: string[]
}

export interface VoicePreparation {
  transcript: string
  draft: VoiceDraft
  duplicates: { lead: Lead; match_on: string }[]
  warnings: string[]
}
```

Add the `/voice` route before creating `VoiceNote.tsx`.

Run:

```bash
cd dashboard && npm run build
```

Expected: module-not-found failure for `VoiceNote`.

- [ ] **Step 2: Implement recorder and file picker**

The page supports `MediaRecorder` when available and an
`<input type="file" accept="audio/*">` fallback. Keep the Blob and object URL in
state. Handle microphone denial with:

```text
Microphone access was denied. You can still attach an audio file or type the note.
```

Do not discard the Blob after transcription errors.

- [ ] **Step 3: Implement review and explicit confirmation**

After preparation, render editable transcript and CRM fields, duplicate links,
warnings, and four choices:

- Add as a new lead.
- Update a selected duplicate with the reviewed non-empty fields and add the
  transcript as a note.
- Open an existing duplicate without writing.
- Cancel/start over.

Confirmation creates a lead with fields, adds the reviewed transcript as a note,
and then optionally processes the lead. Updating uses `api.patchLead` and
`api.addEvent`; it never overwrites an existing field with an empty string. A
create/update failure leaves all edits intact.

- [ ] **Step 4: Add primary navigation entry and build**

Add “Voice note” beside the business-card capture action on the dashboard and
navigation appropriate for small screens.

```bash
cd dashboard && npm run build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 5: Browser smoke**

Against a fake transcription adapter, verify:

1. attach audio;
2. review transcript;
3. edit the name;
4. confirm;
5. land on the created lead;
6. transcript appears as an event;
7. repeat with a duplicate and update the existing row without creating one;
8. cancel creates no row.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src/pages/VoiceNote.tsx dashboard/src/api.ts dashboard/src/App.tsx dashboard/src/pages/Dashboard.tsx
git commit -m "feat: finish reviewed voice-note intake"
```

### Task 4: Mac mini voice setup and verification

**Files:**
- Create: `docs/MAC-MINI-SETUP.md`
- Modify: `docs/LOCAL-AI.md`
- Modify: `docs/CONTRACT.md`
- Modify: `README.md`

**Interfaces:**
- Documents: Apple-silicon 16 GB minimum and local OpenClaw audio configuration.

- [ ] **Step 1: Write the operator path**

Document installation, local model memory guidance, OpenClaw Chat Completions
enablement, local audio model/CLI configuration, `openclaw infer audio
transcribe --file ... --json` verification, `scripts/serve.sh`, and
`scripts/doctor.py --live-agent`.

- [ ] **Step 2: Document failure isolation**

Include exact symptoms for endpoint disabled, model out of memory, transcription
command missing, microphone denied, unsupported audio, and timeout. State that
locality depends on configuring a local provider and there is no app-level cloud
fallback.

- [ ] **Step 3: Run doc commands that are available on this host**

```bash
bash -n scripts/dev.sh scripts/serve.sh scripts/load-env.sh
.venv/bin/python scripts/doctor.py --help
```

Record the Mac mini hardware checklist as operator-run, not locally verified.

- [ ] **Step 4: Commit**

```bash
git add docs/MAC-MINI-SETUP.md docs/LOCAL-AI.md docs/CONTRACT.md README.md
git commit -m "docs: add Mac mini and local voice setup"
```
