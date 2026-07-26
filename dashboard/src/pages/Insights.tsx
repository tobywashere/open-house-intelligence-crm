import { useEffect, useState } from 'react'
import { api } from '../api'
import { computeInsights, Insight, Insights } from '../insights'
import { Skeleton } from '../components/Skeleton'

// Data-mark color validated against the dark surface (dataviz six checks):
// emerald-600 passes lightness band, chroma, and 3:1 contrast.
const BAR_FILL = '#059669'

const SEVERITY: Record<Insight['severity'], { label: string; cls: string }> = {
  warn: { label: 'needs action', cls: 'bg-amber-500/15 text-amber-300 border-amber-500/30' },
  good: { label: 'healthy', cls: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30' },
  info: { label: 'insight', cls: 'bg-zinc-500/15 text-zinc-400 border-zinc-600' },
}

export function InsightsPage() {
  const [result, setResult] = useState<Insights | null>(null)

  useEffect(() => {
    const load = () =>
      Promise.all([api.leads(), api.appointments(), api.audit(200)])
        .then(([leads, appts, audit]) => setResult(computeInsights(leads, appts, audit)))
        .catch(() => {})
    load()
    const t = setInterval(load, 15_000)
    return () => clearInterval(t)
  }, [])

  if (!result)
    return (
      <div className="max-w-5xl grid md:grid-cols-2 gap-4">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-44" />
        ))}
      </div>
    )

  return (
    <div className="max-w-5xl space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Insights</h1>
        <p className="text-sm text-zinc-500 mt-0.5">
          Computed live from the CRM — these same numbers feed tomorrow's morning briefing.
        </p>
      </header>
      <div className="grid md:grid-cols-2 gap-4">
        {result.insights.map((ins, i) => (
          <InsightCard key={ins.id} insight={ins} delay={i * 60} />
        ))}
      </div>
    </div>
  )
}

function InsightCard({ insight, delay }: { insight: Insight; delay: number }) {
  const sev = SEVERITY[insight.severity]
  return (
    <div
      className="rise rounded-xl border border-zinc-800 bg-zinc-900/60 p-5"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-zinc-300">{insight.title}</h2>
        <span className={`ml-auto rounded-full border px-2 py-0.5 text-[10px] ${sev.cls}`}>
          {sev.label}
        </span>
      </div>
      <div className="text-lg font-semibold mt-2">{insight.headline}</div>
      <p className="text-xs text-zinc-500 mt-1">{insight.detail}</p>
      {insight.data.length > 0 && <BarList data={insight.data} />}
    </div>
  )
}

function BarList({ data }: { data: Insight['data'] }) {
  const max = Math.max(...data.map((d) => d.value), 1)
  return (
    <div className="mt-3 space-y-1.5">
      {data.map((d) => (
        <div key={d.label} className="flex items-center gap-2 text-xs group" title={`${d.label}: ${d.display ?? d.value}`}>
          <span className="w-28 shrink-0 truncate text-zinc-400 capitalize">{d.label}</span>
          <div className="flex-1 h-3 rounded-[4px] bg-zinc-800/50 overflow-hidden">
            <div
              className="h-full rounded-[4px] transition-[width] duration-300 group-hover:brightness-125"
              style={{ width: `${Math.max((d.value / max) * 100, d.value > 0 ? 4 : 0)}%`, background: BAR_FILL }}
            />
          </div>
          <span className="w-28 shrink-0 text-right text-zinc-500 tabular-nums">
            {d.display ?? d.value}
          </span>
        </div>
      ))}
    </div>
  )
}
