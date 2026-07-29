import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError, DuplicateCandidate, VoiceDraft } from '../api'
import { toast } from '../components/Toast'

type Phase = 'capture' | 'preview' | 'preparing' | 'review' | 'done'

const EMPTY_DRAFT: VoiceDraft = {
  name: null,
  phone: null,
  email: null,
  budget: null,
  area: null,
  timeline: null,
  intent: 'unknown',
  preferences: [],
  missing_fields: [],
}

const ACCEPTED_AUDIO = 'audio/webm,audio/ogg,audio/wav,audio/x-wav,audio/mp4,audio/m4a,audio/x-m4a,audio/mpeg,audio/mp3'
const MAX_AUDIO_BYTES = 20 * 1024 * 1024

function extensionFor(type: string) {
  if (type.includes('webm')) return 'webm'
  if (type.includes('ogg')) return 'ogg'
  if (type.includes('wav')) return 'wav'
  if (type.includes('mp4') || type.includes('m4a')) return 'm4a'
  return 'mp3'
}

function contentTypeFor(file: File | Blob, filename: string) {
  if (file.type) return file.type
  const ext = filename.split('.').pop()?.toLowerCase()
  if (ext === 'webm') return 'audio/webm'
  if (ext === 'ogg') return 'audio/ogg'
  if (ext === 'wav') return 'audio/wav'
  if (ext === 'm4a' || ext === 'mp4') return 'audio/mp4'
  return 'audio/mpeg'
}

async function toBase64(blob: Blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer())
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000)
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

export function VoiceNotePage() {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>('capture')
  const [audio, setAudio] = useState<{ blob: Blob; filename: string; contentType: string } | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [draft, setDraft] = useState<VoiceDraft>(EMPTY_DRAFT)
  const [duplicates, setDuplicates] = useState<DuplicateCandidate[]>([])
  const [warnings, setWarnings] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [savedLeadId, setSavedLeadId] = useState<number | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!audioUrl) return
    return () => URL.revokeObjectURL(audioUrl)
  }, [audioUrl])

  useEffect(() => () => {
    recorderRef.current?.stop()
    streamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  const keepAudio = (blob: Blob, filename: string) => {
    if (blob.size > MAX_AUDIO_BYTES) {
      toast('⚠ Voice note is too large — maximum 20 MB')
      return
    }
    const contentType = contentTypeFor(blob, filename)
    setAudio({ blob, filename, contentType })
    setAudioUrl(URL.createObjectURL(blob))
    setPhase('preview')
  }

  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      toast('⚠ Recording is not available in this browser — choose an audio file instead')
      fileRef.current?.click()
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const preferred = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/webm']
        .find((type) => MediaRecorder.isTypeSupported(type))
      const recorder = preferred ? new MediaRecorder(stream, { mimeType: preferred }) : new MediaRecorder(stream)
      streamRef.current = stream
      recorderRef.current = recorder
      chunksRef.current = []
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunksRef.current.push(event.data)
      }
      recorder.onstop = () => {
        const type = recorder.mimeType || chunksRef.current[0]?.type || 'audio/webm'
        const blob = new Blob(chunksRef.current, { type })
        stream.getTracks().forEach((track) => track.stop())
        streamRef.current = null
        recorderRef.current = null
        setRecording(false)
        if (blob.size) keepAudio(blob, `voice-note.${extensionFor(type)}`)
      }
      recorder.start()
      setRecording(true)
    } catch {
      toast('⚠ Microphone access was not available — choose an audio file instead')
    }
  }

  const stopRecording = () => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
  }

  const prepare = async () => {
    if (!audio) return
    setPhase('preparing')
    try {
      const result = await api.prepareVoiceNote(
        audio.filename,
        audio.contentType,
        await toBase64(audio.blob),
      )
      setTranscript(result.transcript)
      setDraft(result.draft)
      setDuplicates(result.duplicates)
      setWarnings(result.warnings)
      setPhase('review')
    } catch (error) {
      const detail = error instanceof ApiError ? error.detail : undefined
      toast(`⚠ ${detail || 'Voice note could not be prepared. Your recording is still here.'}`)
      setPhase('preview')
    }
  }

  const reset = () => {
    setAudio(null)
    setAudioUrl(null)
    setTranscript('')
    setDraft(EMPTY_DRAFT)
    setDuplicates([])
    setWarnings([])
    setSavedLeadId(null)
    setPhase('capture')
  }

  const leadFields = () => ({
    name: draft.name?.trim() || undefined,
    phone: draft.phone?.trim() || undefined,
    email: draft.email?.trim() || undefined,
    budget: draft.budget ?? undefined,
    area: draft.area?.trim() || undefined,
    timeline: draft.timeline?.trim() || undefined,
    intent: draft.intent,
  })

  const saveNew = async () => {
    if (busy || !draft.name?.trim()) return
    setBusy(true)
    try {
      const lead = await api.createLeadFields({
        ...leadFields(),
        name: draft.name.trim(),
        source: 'note',
      })
      if (transcript.trim()) await api.addEvent(lead.id, 'note', transcript.trim())
      await api.processLead(lead.id).catch(() => {})
      setSavedLeadId(lead.id)
      setPhase('done')
      toast(`✓ ${lead.name} added from your voice note`)
    } catch (error) {
      const detail = error instanceof ApiError ? error.detail : undefined
      toast(`⚠ Couldn’t add the lead${detail ? ` — ${detail}` : ''}`)
    } finally {
      setBusy(false)
    }
  }

  const updateExisting = async (candidate: DuplicateCandidate) => {
    if (busy) return
    setBusy(true)
    try {
      const fields = leadFields()
      const patch = Object.fromEntries(
        Object.entries(fields).filter(([, value]) => value !== undefined),
      )
      if (Object.keys(patch).length) await api.patchLead(candidate.lead.id, patch)
      if (transcript.trim()) await api.addEvent(candidate.lead.id, 'note', transcript.trim())
      setSavedLeadId(candidate.lead.id)
      setPhase('done')
      toast(`✓ ${candidate.lead.name} updated from your voice note`)
    } catch (error) {
      const detail = error instanceof ApiError ? error.detail : undefined
      toast(`⚠ Couldn’t update the lead${detail ? ` — ${detail}` : ''}`)
    } finally {
      setBusy(false)
    }
  }

  const field = (
    key: 'name' | 'phone' | 'email' | 'area' | 'timeline',
    label: string,
  ) => (
    <label className="block text-xs text-sub">
      <span className="uppercase tracking-wider text-[10px]">{label}</span>
      <input
        value={draft[key] ?? ''}
        onChange={(event) => setDraft({ ...draft, [key]: event.target.value || null })}
        className="mt-1 w-full rounded-md bg-bg border border-tile px-3 py-2 text-sm text-body
                   focus:outline-none focus:border-accent"
      />
    </label>
  )

  return (
    <div className="max-w-2xl mx-auto space-y-4">
      <nav className="flex items-center gap-2 text-sm text-sub/80">
        <button onClick={() => navigate(-1)} className="hover:text-ink2">← Back</button>
        <span className="text-sub/40">/</span>
        <span className="text-body">Add from voice note</span>
      </nav>

      {phase === 'capture' && (
        <section className="rounded-2xl border border-tile bg-surface p-8 text-center space-y-5">
          <div>
            <h1 className="text-lg font-semibold text-ink">Talk through a new lead</h1>
            <p className="text-sm text-sub mt-1">
              Record a note or choose audio. You will review every field before anything is saved.
            </p>
          </div>
          <div className="flex flex-wrap justify-center gap-3">
            {!recording ? (
              <button
                onClick={startRecording}
                className="rounded-lg bg-accent text-[#0b0f19] hover:brightness-110 px-5 py-2 text-sm font-medium"
              >
                ● Start recording
              </button>
            ) : (
              <button
                onClick={stopRecording}
                className="rounded-lg bg-alert text-[#0b0f19] hover:brightness-110 px-5 py-2 text-sm font-medium"
              >
                ■ Stop recording
              </button>
            )}
            <button
              onClick={() => fileRef.current?.click()}
              disabled={recording}
              className="rounded-lg border border-line hover:border-accent/60 disabled:opacity-40 px-5 py-2 text-sm"
            >
              Choose audio file
            </button>
          </div>
          {recording && <div className="text-sm text-alert animate-pulse">Recording…</div>}
        </section>
      )}

      {(phase === 'preview' || phase === 'preparing') && audioUrl && (
        <section className="rounded-2xl border border-tile bg-surface p-5 space-y-4">
          <h1 className="text-sm font-semibold text-ink">Listen before transcribing</h1>
          <audio controls src={audioUrl} className="w-full" />
          <p className="text-xs text-sub">
            Audio is sent only to this CRM server, transcribed locally, then deleted from its temporary folder.
          </p>
          <div className="flex gap-2">
            <button onClick={reset} disabled={phase === 'preparing'} className="rounded-lg border border-line px-4 py-1.5 text-sm disabled:opacity-40">
              Start over
            </button>
            <button
              onClick={prepare}
              disabled={phase === 'preparing'}
              className="ml-auto rounded-lg bg-accent text-[#0b0f19] px-4 py-1.5 text-sm font-medium disabled:opacity-40"
            >
              {phase === 'preparing' ? 'Transcribing locally…' : 'Transcribe and review →'}
            </button>
          </div>
        </section>
      )}

      {phase === 'review' && (
        <section className="rounded-2xl border border-tile bg-surface p-5 space-y-4">
          <div>
            <h1 className="text-sm font-semibold text-ink">Review before saving</h1>
            <p className="text-xs text-sub mt-1">Nothing has been written to the CRM yet.</p>
          </div>
          {warnings.map((warning) => (
            <div key={warning} className="rounded-lg border border-alert/30 bg-alert/10 px-3 py-2 text-xs text-alert">
              {warning}
            </div>
          ))}
          <label className="block text-xs text-sub">
            <span className="uppercase tracking-wider text-[10px]">Transcript</span>
            <textarea
              value={transcript}
              onChange={(event) => setTranscript(event.target.value)}
              rows={5}
              className="mt-1 w-full rounded-md bg-bg border border-tile px-3 py-2 text-sm text-body resize-y
                         focus:outline-none focus:border-accent"
            />
          </label>
          <div className="grid sm:grid-cols-2 gap-3">
            {field('name', 'Name')}
            {field('phone', 'Phone')}
            {field('email', 'Email')}
            {field('area', 'Area')}
            {field('timeline', 'Timeline')}
            <label className="block text-xs text-sub">
              <span className="uppercase tracking-wider text-[10px]">Budget</span>
              <input
                type="number"
                min="0"
                value={draft.budget ?? ''}
                onChange={(event) => setDraft({
                  ...draft,
                  budget: event.target.value ? Number(event.target.value) : null,
                })}
                className="mt-1 w-full rounded-md bg-bg border border-tile px-3 py-2 text-sm text-body
                           focus:outline-none focus:border-accent"
              />
            </label>
          </div>
          <label className="block text-xs text-sub">
            <span className="uppercase tracking-wider text-[10px]">Intent</span>
            <select
              value={draft.intent}
              onChange={(event) => setDraft({ ...draft, intent: event.target.value as VoiceDraft['intent'] })}
              className="mt-1 w-full rounded-md bg-bg border border-tile px-3 py-2 text-sm text-body"
            >
              <option value="unknown">Not sure</option>
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
              <option value="browse">Browsing</option>
            </select>
          </label>

          {duplicates.length > 0 && (
            <div className="rounded-xl border border-alert/30 bg-alert/5 p-3 space-y-2">
              <div className="text-xs font-medium text-alert">Possible existing lead</div>
              {duplicates.map((candidate) => (
                <div key={candidate.lead.id} className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="text-body">
                    {candidate.lead.name} — same {candidate.match_on}
                  </span>
                  <Link to={`/lead/${candidate.lead.id}`} className="ml-auto text-accent hover:underline">
                    Open without saving
                  </Link>
                  <button
                    onClick={() => updateExisting(candidate)}
                    disabled={busy}
                    className="rounded-md border border-accent/50 px-2.5 py-1 text-accent disabled:opacity-40"
                  >
                    {busy ? 'Saving…' : 'Update this lead'}
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex flex-wrap gap-2 pt-1">
            <button onClick={reset} disabled={busy} className="rounded-lg border border-line px-4 py-1.5 text-sm disabled:opacity-40">
              Cancel
            </button>
            <button
              onClick={saveNew}
              disabled={busy || !draft.name?.trim()}
              className="ml-auto rounded-lg bg-accent text-[#0b0f19] px-4 py-1.5 text-sm font-medium disabled:opacity-40"
            >
              {busy ? 'Saving…' : duplicates.length ? 'Add as a new lead anyway' : 'Add new lead ✓'}
            </button>
          </div>
        </section>
      )}

      {phase === 'done' && savedLeadId && (
        <section className="rounded-2xl border border-accent/30 bg-accent/5 p-6 text-center space-y-3">
          <div className="text-2xl">✓</div>
          <div className="text-sm text-body">Your confirmed changes are in the CRM.</div>
          <div className="flex justify-center gap-2">
            <Link to={`/lead/${savedLeadId}`} className="rounded-lg bg-accent text-[#0b0f19] px-4 py-1.5 text-sm font-medium">
              Open profile →
            </Link>
            <button onClick={reset} className="rounded-lg border border-line px-4 py-1.5 text-sm">
              Add another
            </button>
          </div>
        </section>
      )}

      <input
        ref={fileRef}
        type="file"
        accept={ACCEPTED_AUDIO}
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (file) keepAudio(file, file.name)
        }}
      />
    </div>
  )
}
