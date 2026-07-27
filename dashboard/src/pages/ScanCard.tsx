import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api'
import { toast } from '../components/Toast'

// Scan a business card → agent extracts → review → confirm → lead created.
// No live viewfinder: getUserMedia needs a secure context and the app is served
// over plain http on the LAN. The hidden file input with capture="environment"
// opens the native camera app on phones; elsewhere it's a plain file picker.
type Phase = 'capture' | 'preview' | 'extracting' | 'review' | 'done'

interface Extracted {
  name?: string
  phone?: string
  email?: string
  area?: string
  intent?: string
  raw_text?: string
}

// Downscale to ≤1600px on the longest side (never upscale) and re-encode as
// JPEG — keeps uploads well under the backend's ~8 MB decoded limit.
const MAX_DIM = 1600
async function downscale(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file)
  try {
    const scale = Math.min(1, MAX_DIM / Math.max(bitmap.width, bitmap.height))
    const w = Math.max(1, Math.round(bitmap.width * scale))
    const h = Math.max(1, Math.round(bitmap.height * scale))
    const canvas = document.createElement('canvas')
    canvas.width = w
    canvas.height = h
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0, w, h)
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.85),
    )
    if (!blob) throw new Error('JPEG encode failed')
    return blob
  } finally {
    bitmap.close()
  }
}

export function ScanCardPage() {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>('capture')
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [blob, setBlob] = useState<Blob | null>(null)
  const [fields, setFields] = useState<Extracted>({})
  const [dupes, setDupes] = useState<{ lead: { id: number; name: string }; match_on: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [newLeadId, setNewLeadId] = useState<number | null>(null)

  const fileRef = useRef<HTMLInputElement>(null)

  // revoke each object URL once it's replaced or the page unmounts
  useEffect(() => {
    if (!imageUrl) return
    return () => URL.revokeObjectURL(imageUrl)
  }, [imageUrl])

  const onFile = async (f: File | undefined) => {
    if (!f) return
    try {
      const jpeg = await downscale(f)
      setBlob(jpeg)
      setImageUrl(URL.createObjectURL(jpeg))
      setPhase('preview')
    } catch {
      // e.g. HEIC the browser can't decode
      toast('⚠ Couldn’t read that image — try a JPEG or PNG photo')
    }
  }

  const extract = async () => {
    if (!blob) return
    setPhase('extracting')
    try {
      const buf = new Uint8Array(await blob.arrayBuffer())
      let bin = ''
      for (let i = 0; i < buf.length; i += 0x8000)
        bin += String.fromCharCode(...buf.subarray(i, i + 0x8000))
      const res = await api.scanCard('card.jpg', btoa(bin))
      setFields(res.extracted ?? {})
      setDupes(res.duplicates ?? [])
      setPhase('review')
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : undefined
      toast(detail ? `⚠ ${detail}` : '⚠ Scan failed — is the backend running?')
      setPhase('preview')
    }
  }

  const confirm = async () => {
    if (busy) return
    setBusy(true)
    try {
      const lead = await api.createLeadFields({
        name: fields.name || 'Scanned lead',
        phone: fields.phone || undefined,
        email: fields.email || undefined,
        area: fields.area || undefined,
        intent: fields.intent || undefined,
        source: 'form',
      })
      if (fields.raw_text) await api.addEvent(lead.id, 'note', fields.raw_text)
      await api.processLead(lead.id).catch(() => {})
      setNewLeadId(lead.id)
      setPhase('done')
      toast(`✓ ${lead.name} added from business card`)
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : undefined
      toast(`⚠ Couldn’t create the lead${detail ? ` — ${detail}` : ''}`)
      // stay on review so nothing typed is lost
    } finally {
      setBusy(false)
    }
  }

  const retake = () => {
    setBlob(null)
    setImageUrl(null)
    setFields({})
    setDupes([])
    setPhase('capture')
  }

  return (
    <div className="max-w-xl mx-auto space-y-4">
      <nav className="flex items-center gap-2 text-sm text-sub/80">
        <button onClick={() => navigate(-1)} className="hover:text-ink2">← Back</button>
        <span className="text-sub/40">/</span>
        <span className="text-body">Scan business card</span>
      </nav>

      {phase === 'capture' && (
        <div className="rounded-2xl border border-tile bg-surface overflow-hidden">
          <button
            onClick={() => fileRef.current?.click()}
            className="w-full flex flex-col items-center justify-center gap-3 py-16 px-6
                       hover:bg-bg/60 transition-colors"
          >
            <span className="text-4xl">📷</span>
            <span className="text-sm font-medium text-body">Attach a photo of the card</span>
            <span className="text-xs text-sub">Opens the camera on phones — or pick an image file</span>
          </button>
        </div>
      )}

      {(phase === 'preview' || phase === 'extracting') && imageUrl && (
        <div className="rounded-2xl border border-tile bg-surface overflow-hidden">
          <img src={imageUrl} alt="Business card" className="w-full max-h-80 object-contain bg-black" />
          <div className="flex items-center justify-center gap-3 py-3">
            {phase === 'extracting' ? (
              <div className="text-sm text-sub animate-pulse py-1">✦ Agent is reading the card…</div>
            ) : (
              <>
                <button onClick={retake} className="rounded-lg border border-line hover:border-[#4b5563] px-4 py-1.5 text-sm">
                  Retake
                </button>
                <button
                  onClick={extract}
                  className="rounded-lg bg-accent text-[#0b0f19] hover:brightness-110 px-4 py-1.5 text-sm font-medium"
                >
                  Use photo →
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {phase === 'review' && (
        <div className="rounded-2xl border border-tile bg-surface p-4 space-y-3">
          <h2 className="text-sm font-semibold text-ink">Review before adding</h2>
          {dupes.length > 0 && (
            <div className="rounded-lg border border-alert/30 bg-alert/10 px-3 py-2 text-xs">
              <span className="text-alert">Possible duplicate: </span>
              {dupes.map((d) => (
                <Link key={d.lead.id} to={`/lead/${d.lead.id}`} className="text-accent hover:underline">
                  {d.lead.name} (same {d.match_on})
                </Link>
              ))}
            </div>
          )}
          {(['name', 'phone', 'email', 'area'] as const).map((k) => (
            <label key={k} className="block text-xs text-sub">
              <span className="uppercase tracking-wider text-[10px]">{k}</span>
              <input
                value={fields[k] ?? ''}
                onChange={(e) => setFields({ ...fields, [k]: e.target.value })}
                className="mt-1 w-full rounded-md bg-bg border border-tile px-3 py-2 text-sm text-body
                           focus:outline-none focus:border-accent"
              />
            </label>
          ))}
          <label className="block text-xs text-sub">
            <span className="uppercase tracking-wider text-[10px]">Card notes</span>
            <textarea
              value={fields.raw_text ?? ''}
              onChange={(e) => setFields({ ...fields, raw_text: e.target.value })}
              rows={3}
              className="mt-1 w-full rounded-md bg-bg border border-tile px-3 py-2 text-sm text-body resize-y
                         focus:outline-none focus:border-accent"
            />
          </label>
          <div className="flex gap-2 pt-1">
            <button onClick={retake} className="rounded-lg border border-line hover:border-[#4b5563] px-4 py-1.5 text-sm">
              Start over
            </button>
            <button
              onClick={confirm}
              disabled={busy || !(fields.name ?? '').trim()}
              className="ml-auto rounded-lg bg-accent text-[#0b0f19] hover:brightness-110 disabled:opacity-40
                         px-4 py-1.5 text-sm font-medium"
            >
              {busy ? 'Adding…' : 'Add lead ✓'}
            </button>
          </div>
        </div>
      )}

      {phase === 'done' && newLeadId && (
        <div className="rounded-2xl border border-accent/30 bg-accent/5 p-6 text-center space-y-3">
          <div className="text-2xl">✓</div>
          <div className="text-sm text-body">{fields.name} is in the CRM.</div>
          <div className="flex justify-center gap-2">
            <Link to={`/lead/${newLeadId}`} className="rounded-lg bg-accent text-[#0b0f19] hover:brightness-110 px-4 py-1.5 text-sm font-medium">
              Open profile →
            </Link>
            <button onClick={retake} className="rounded-lg border border-line hover:border-[#4b5563] px-4 py-1.5 text-sm">
              Scan another
            </button>
          </div>
        </div>
      )}

      {/* capture="environment" opens the native camera app on phones */}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          e.target.value = '' // allow re-picking the same file (e.g. after a decode failure)
          void onFile(f)
        }}
      />
    </div>
  )
}
