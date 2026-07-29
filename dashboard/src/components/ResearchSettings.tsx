import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, ApiError, ResearchSettings as Settings } from '../api'
import { Skeleton } from './Skeleton'
import { toast } from './Toast'

// Editor for the daily market-research scope. Opened from the market-watch
// section of the daily summary, which is where an operator actually notices
// the research is off-target.
//
// The rendered prompt is shown read-only underneath the fields: the operator
// should be able to see exactly what the agent will be asked, rather than
// trusting that their keywords made it in. It refreshes from the PUT response
// on save, so what is on screen is always the server's rendering, never a
// guess assembled on the client.

// Lists are edited one-per-line. A textarea beats chips here: these are
// phrases ("ADU legislation", "King County permit data"), they get pasted in
// bulk, and a line-per-item survives that without any parsing ceremony.
const toLines = (items: string[]) => items.join('\n')
const fromLines = (text: string) =>
  text.split('\n').map((s) => s.trim()).filter(Boolean)

export function ResearchSettings({ onClose }: { onClose: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [failed, setFailed] = useState(false)
  const [saving, setSaving] = useState(false)

  const [role, setRole] = useState('')
  const [audience, setAudience] = useState('')
  const [lookback, setLookback] = useState(7)
  const [regions, setRegions] = useState('')
  const [topics, setTopics] = useState('')
  const [exclusions, setExclusions] = useState('')

  const hydrate = (s: Settings) => {
    setSettings(s)
    setRole(s.role ?? '')
    setAudience(s.audience ?? '')
    setLookback(s.lookback_days ?? 7)
    setRegions(toLines(s.regions ?? []))
    setTopics(toLines(s.topics ?? []))
    setExclusions(toLines(s.exclusions ?? []))
  }

  useEffect(() => {
    api.researchSettings().then(hydrate).catch(() => setFailed(true))
  }, [])

  const save = async () => {
    if (saving || !settings) return
    // the backend enforces these too (422); checking here turns a rejected
    // round-trip into an immediate, specific message
    if (!role.trim() || !audience.trim()) {
      toast('⚠ Role and audience cannot be empty')
      return
    }
    if (fromLines(regions).length === 0) {
      toast('⚠ Add at least one region')
      return
    }
    setSaving(true)
    try {
      const saved = await api.saveResearchSettings({
        role: role.trim(),
        audience: audience.trim(),
        lookback_days: lookback,
        regions: fromLines(regions),
        topics: fromLines(topics),
        exclusions: fromLines(exclusions),
        national_scope_note: settings.national_scope_note ?? '',
      })
      hydrate(saved)
      toast('✓ Research scope saved — tomorrow’s research uses it')
    } catch (e) {
      toast(`⚠ ${e instanceof ApiError ? e.detail ?? e.message : 'Could not save'}`)
    } finally {
      setSaving(false)
    }
  }

  const field = 'w-full rounded-lg border border-line bg-tile px-3 py-2 text-sm ' +
    'placeholder:text-sub/50 focus:border-accent/60 focus:outline-none'
  const label = 'block text-xs font-medium text-sub mb-1'

  // Portalled to <body> deliberately. The daily-summary overlay this opens
  // from sets backdrop-blur, and a backdrop-filter establishes a containing
  // block for fixed-position descendants — rendered inline, this panel's
  // `fixed inset-0` resolved against that scrolled container instead of the
  // viewport and opened clipped off the top of the screen.
  return createPortal(
    // Its own backdrop swallows the click so dismissing this panel never
    // also dismisses the overlay underneath it.
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-bg/80 backdrop-blur-sm p-6"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-2xl rounded-xl border border-tile bg-surface p-5 my-6"
      >
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-base font-semibold">Research scope</h2>
          <button onClick={onClose} className="text-sub hover:text-ink transition-colors" aria-label="Close">
            ✕
          </button>
        </div>
        <p className="text-xs text-sub/70 mb-4">
          What the agent searches for each morning. Saved here, it overrides the vertical
          pack’s defaults.
        </p>

        {failed ? (
          <p className="text-sm text-alert">
            Couldn’t load the research settings — is the backend running?
          </p>
        ) : !settings ? (
          <Skeleton className="h-64" />
        ) : (
          <div className="space-y-4">
            <div className="grid sm:grid-cols-2 gap-3">
              <div>
                <label className={label} htmlFor="rs-role">Role</label>
                <input id="rs-role" className={field} value={role} onChange={(e) => setRole(e.target.value)} />
              </div>
              <div>
                <label className={label} htmlFor="rs-audience">Audience</label>
                <input id="rs-audience" className={field} value={audience} onChange={(e) => setAudience(e.target.value)} />
              </div>
            </div>

            <div>
              <label className={label} htmlFor="rs-lookback">Lookback (days)</label>
              <input
                id="rs-lookback"
                type="number"
                min={1}
                max={90}
                value={lookback}
                onChange={(e) => setLookback(Number(e.target.value))}
                className={`${field} w-32`}
              />
            </div>

            {([
              ['Regions', regions, setRegions, 'One per line — e.g. Bellevue'],
              ['Topics', topics, setTopics, 'One per line — e.g. interest rates'],
              ['Exclusions', exclusions, setExclusions, 'One per line — skip these'],
            ] as const).map(([name, value, set, hint]) => (
              <div key={name}>
                <label className={label} htmlFor={`rs-${name}`}>
                  {name} <span className="font-normal text-sub/60">· {hint}</span>
                </label>
                <textarea
                  id={`rs-${name}`}
                  rows={name === 'Regions' ? 4 : 3}
                  className={`${field} font-mono text-xs`}
                  value={value}
                  onChange={(e) => set(e.target.value)}
                />
              </div>
            ))}

            <div>
              <div className={label}>
                Prompt the agent will receive <span className="font-normal text-sub/60">· read-only</span>
              </div>
              <pre className="max-h-56 overflow-auto rounded-lg border border-tile bg-tile p-3 text-[11px] leading-relaxed text-sub whitespace-pre-wrap">
                {settings.rendered_prompt || '(no prompt template found on the server)'}
              </pre>
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <button onClick={onClose} className="rounded-lg border border-line px-4 py-2 text-sm text-sub hover:text-ink transition-colors">
                Cancel
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="rounded-lg bg-accent/90 hover:bg-accent px-4 py-2 text-sm font-medium text-bg transition-colors disabled:opacity-40"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
