import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, AuditRow, fmtDate } from '../api'

const ACTOR_STYLE: Record<string, string> = {
  agent: 'text-accent',
  cron: 'text-[#a5b4fc]',
  user: 'text-ink2',
}

// The wow factor: a live stream of every tool call the agent makes.
export function AuditLog({ full = false }: { full?: boolean }) {
  const [rows, setRows] = useState<AuditRow[]>([])
  const maxSeen = useRef<number | null>(null)

  useEffect(() => {
    const load = () =>
      api
        .audit(full ? 100 : 30)
        .then((r) => {
          setRows(r)
          if (maxSeen.current === null && r.length) maxSeen.current = r[0].id
        })
        .catch(() => {})
    load()
    const t = setInterval(load, 3000)
    return () => clearInterval(t)
  }, [full])

  const isNew = (id: number) => maxSeen.current !== null && id > maxSeen.current

  return (
    <div className={full ? 'max-w-3xl' : ''}>
      {full && <h1 className="text-xl font-semibold mb-4">Agent activity</h1>}
      <div className="space-y-1.5 font-mono text-xs">
        {rows.map((r) => (
          <div key={r.id} className={`flex gap-2 items-baseline rounded px-1 ${isNew(r.id) ? 'row-flash' : ''}`}>
            <span className="text-sub/60 shrink-0">{fmtDate(r.ts)}</span>
            <span className={`shrink-0 ${ACTOR_STYLE[r.actor] ?? ''}`}>{r.actor}</span>
            <span className="text-body">{r.tool}</span>
            {r.lead_id && (
              <Link to={`/lead/${r.lead_id}`} className="text-sub hover:text-accent truncate">
                → {r.lead_name ?? `lead #${r.lead_id}`}
              </Link>
            )}
            <span className="text-sub/60 truncate">{summarize(r.output)}</span>
          </div>
        ))}
        {!rows.length && <div className="text-sub/60">No agent activity yet.</div>}
      </div>
    </div>
  )
}

function summarize(outputJson: string): string {
  try {
    const o = JSON.parse(outputJson)
    const parts = Object.entries(o)
      .slice(0, 2)
      .map(([k, v]) => `${k}: ${typeof v === 'string' ? v.slice(0, 40) : JSON.stringify(v)}`)
    return parts.join(', ')
  } catch {
    return ''
  }
}
