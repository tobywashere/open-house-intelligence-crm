import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { toast } from '../components/Toast'

// Scan a business card → agent extracts → review → confirm → lead created.
// Live viewfinder when a camera exists (phone or laptop); file picker always.
type Phase = 'capture' | 'preview' | 'extracting' | 'review' | 'done'

interface Extracted {
  name?: string
  phone?: string
  email?: string
  area?: string
  intent?: string
  raw_text?: string
}

export function ScanCardPage() {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>('capture')
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [blob, setBlob] = useState<Blob | null>(null)
  const [fields, setFields] = useState<Extracted>({})
  const [dupes, setDupes] = useState<{ lead: { id: number; name: string }; match_on: string }[]>([])
  const [busy, setBusy] = useState(false)
  const [camReady, setCamReady] = useState(false)
  const [newLeadId, setNewLeadId] = useState<number | null>(null)

  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // live viewfinder — no user-agent sniffing: works wherever a camera exists
  useEffect(() => {
    if (phase !== 'capture') return
    let cancelled = false
    navigator.mediaDevices
      ?.getUserMedia({ video: { facingMode: 'environment' } })
      .then((stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          videoRef.current.play().catch(() => {})
        }
        setCamReady(true)
      })
      .catch(() => setCamReady(false))
    return () => {
      cancelled = true
      streamRef.current?.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
  }, [phase])

  const stopCamera = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setCamReady(false)
  }

  const shoot = () => {
    const video = videoRef.current
    if (!video) return
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d')!.drawImage(video, 0, 0)
    canvas.toBlob((b) => {
      if (!b) return
      setBlob(b)
      setImageUrl(URL.createObjectURL(b))
      stopCamera()
      setPhase('preview')
    }, 'image/jpeg', 0.92)
  }

  const onFile = (f: File | undefined) => {
    if (!f) return
    setBlob(f)
    setImageUrl(URL.createObjectURL(f))
    stopCamera()
    setPhase('preview')
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
    } catch {
      toast('⚠ Scan failed — is the backend running?')
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
          {/* instagram-style viewfinder */}
          <div className="relative bg-black aspect-[4/3]">
            <video ref={videoRef} playsInline muted className="absolute inset-0 h-full w-full object-cover" />
            {camReady ? (
              <>
                {/* card frame guide */}
                <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                  <div className="w-[78%] aspect-[1.75] rounded-xl border-2 border-white/60" />
                </div>
                <div className="absolute top-3 inset-x-0 text-center text-xs text-white/70">
                  Line the card up inside the frame
                </div>
              </>
            ) : (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sub text-sm">
                <span className="text-3xl">📷</span>
                No camera available — choose a photo instead
              </div>
            )}
          </div>
          <div className="flex items-center justify-center gap-8 py-4 bg-bg">
            <button
              onClick={() => fileRef.current?.click()}
              className="text-xs text-body border border-line hover:border-accent/60 hover:text-accent
                         rounded-full px-3 py-1.5 transition-colors"
            >
              🖼 Choose file
            </button>
            {camReady && (
              <button
                onClick={shoot}
                aria-label="Take photo"
                className="h-16 w-16 rounded-full border-4 border-ink bg-ink/20 hover:bg-accent/40
                           active:scale-95 transition-all"
              />
            )}
            <span className="w-[76px]" />
          </div>
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
        onChange={(e) => onFile(e.target.files?.[0])}
      />
    </div>
  )
}
