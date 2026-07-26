import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { FunnelData, fetchFunnel } from '../funnel'
import { Skeleton } from '../components/Skeleton'

// Sales funnel page — concept-mock layout (docs/FUNNEL-UI.md).
// Marks are single-hue (#0284c7 family); identity lives in labels.
export function FunnelPage() {
  const [data, setData] = useState<FunnelData | null>(null)

  useEffect(() => {
    let live = true
    const load = (force = false) => fetchFunnel(force).then((d) => live && setData(d)).catch(() => {})
    load(true)
    const t = setInterval(() => load(true), 60_000)
    return () => {
      live = false
      clearInterval(t)
    }
  }, [])

  if (!data)
    return (
      <div className="max-w-6xl space-y-4">
        <Skeleton className="h-80 w-full" />
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
          {Array.from({ length: 4 }, (_, i) => (
            <Skeleton key={i} className="h-48" />
          ))}
        </div>
        <Skeleton className="h-28 w-full" />
      </div>
    )

  return (
    <div className="max-w-6xl space-y-4">
      {/* funnel + summary */}
      <section className="rise rounded-xl border border-tile bg-surface p-5">
        <h1 className="text-lg font-semibold text-ink">Sales funnel</h1>
        <p className="text-xs text-sub mb-5">Your buyer pipeline from new lead to closed deal.</p>
        <div className="grid lg:grid-cols-[1fr_260px] gap-6 items-start">
          <FunnelChart data={data} />
          <aside className="space-y-4 text-sm">
            <div>
              <div className="text-xs text-sub">Overall conversion</div>
              <div className="text-3xl font-semibold text-accent mt-0.5">{data.overallPct}%</div>
              <div className="text-xs text-sub mt-0.5">{data.overallLabel}</div>
            </div>
            <div className="border-t border-tile pt-3">
              <div className="text-xs text-sub">Biggest bottleneck</div>
              <div className="font-semibold text-ink mt-0.5">{data.bottleneck.label}</div>
              <p className="text-xs text-sub mt-1">{data.bottleneck.detail}</p>
            </div>
            <div className="border-t border-tile pt-3">
              <div className="text-xs text-sub">Time to close</div>
              <div className="text-2xl font-semibold text-accent mt-0.5">
                {data.avgDaysToClose != null ? `${data.avgDaysToClose} day${data.avgDaysToClose === 1 ? '' : 's'}` : '—'}
              </div>
              <div className="text-xs text-sub mt-0.5">avg from new lead to closed</div>
            </div>
          </aside>
        </div>
      </section>

      {/* analytics cards */}
      <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Card title="Lead source conversion" sub="Share of each source reaching a tour or close" delay={60}>
          <div className="space-y-1.5">
            {data.sources.map((s) => (
              <div key={s.label} className="flex items-center gap-2 text-[11px]" title={`${s.label}: ${s.won}/${s.total}`}>
                <span className="w-16 shrink-0 truncate text-sub capitalize">{s.label}</span>
                <div className="flex-1 h-2.5 rounded-[4px] bg-tile/60 overflow-hidden">
                  <div
                    className="h-full rounded-[4px]"
                    style={{ width: `${Math.max(s.rate, s.won > 0 ? 5 : 0)}%`, background: '#0284c7' }}
                  />
                </div>
                <span className="w-20 shrink-0 text-right text-sub/80 tabular-nums">
                  {s.rate}% ({s.won}/{s.total})
                </span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Stage velocity" sub="Average time spent in each stage" delay={120}>
          <div className="space-y-2">
            {data.velocity.map((v) => (
              <div key={v.stage} className="flex items-center gap-2 text-xs">
                <span className="text-sub">{v.stage}</span>
                <span className="ml-auto tabular-nums text-body">
                  {v.days != null ? `${v.days} days` : '—'}
                </span>
                {v.slow && (
                  <span className="rounded-full bg-alert/10 border border-alert/30 text-alert px-1.5 text-[10px]">
                    slow
                  </span>
                )}
              </div>
            ))}
            <p className="text-[10px] text-sub/60 pt-1">
              From status-change timestamps; fills in as leads move.
            </p>
          </div>
        </Card>

        <Card title="Top opportunities" sub="Warm buyers most likely to close" delay={180}>
          <div className="space-y-2">
            {data.opportunities.map((o) => (
              <Link key={o.lead.id} to={`/lead/${o.lead.id}`} className="flex items-center gap-2 group">
                <span className="h-7 w-7 shrink-0 rounded-full bg-accent/10 border border-accent/30 text-accent
                                 text-[10px] font-semibold flex items-center justify-center">
                  {o.lead.name.split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase()}
                </span>
                <span className="min-w-0">
                  <span className="block text-xs font-medium text-body truncate group-hover:text-accent">
                    {o.lead.name}
                  </span>
                  <span className="block text-[10px] text-sub truncate capitalize">
                    {o.stageLabel} · {o.valueLabel} · {o.estimate}
                  </span>
                </span>
                <span className={`ml-auto shrink-0 text-[10px] ${o.heat === 'High' ? 'text-accent' : 'text-sub'}`}>
                  {o.heat} ●
                </span>
              </Link>
            ))}
          </div>
        </Card>

        <Card title="Demand by area" sub="Active buyer demand" delay={240}>
          <DemandMini />
        </Card>
      </div>

      {/* next best actions */}
      {data.actions.length > 0 && (
        <section className="rise rounded-xl border border-tile bg-surface p-4" style={{ animationDelay: '300ms' }}>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-accent">◎</span>
            <h2 className="text-sm font-semibold text-ink">Next best actions</h2>
            <span className="text-xs text-sub">Prioritized recommendations to move your pipeline forward.</span>
          </div>
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
            {data.actions.map((a) => (
              <div key={a.title} className="rounded-lg border border-tile bg-tile/40 p-3 flex flex-col">
                <div className="flex items-start gap-2">
                  <span>{a.icon}</span>
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-body">{a.title}</div>
                    <div className="text-[10px] text-sub mt-0.5">{a.sub}</div>
                  </div>
                </div>
                <div className="flex items-center justify-between mt-2 pt-1">
                  <span className={`text-[10px] ${a.impact === 'High impact' ? 'text-accent' : 'text-sub'}`}>
                    {a.impact}
                  </span>
                  <Link
                    to={a.to}
                    className="rounded-md border border-line hover:border-accent/60 px-2 py-0.5 text-[10px]
                               text-body hover:text-accent transition-colors"
                  >
                    {a.cta}
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function FunnelChart({ data }: { data: FunnelData }) {
  const max = Math.max(...data.stages.map((s) => s.count), 1)
  return (
    <div className="flex gap-3">
      <div className="flex-1 space-y-1.5 min-w-0">
        {data.stages.map((s, i) => {
          const w = Math.max((s.count / max) * 100, 22)
          return (
            <div
              key={s.key}
              className="mx-auto relative h-[46px] flex flex-col items-center justify-center transition-all"
              style={{
                width: `${w}%`,
                clipPath: 'polygon(0 0, 100% 0, 94% 100%, 6% 100%)',
                background: 'linear-gradient(180deg, rgba(2,132,199,0.45), rgba(2,132,199,0.12))',
                boxShadow: 'inset 0 1px 0 rgba(56,189,248,0.5), 0 0 18px rgba(14,165,233,0.12)',
              }}
              title={`${s.label}: ${s.count}`}
            >
              <span className="text-[11px] text-sub leading-none">{s.label}</span>
              <span className="text-lg font-semibold text-ink leading-tight">{s.count}</span>
            </div>
          )
        })}
      </div>
      {/* conversion rail: one chip per transition, aligned to the gaps */}
      <div className="w-36 shrink-0 flex flex-col justify-between py-[28px]">
        {data.conversions.map((c, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <span className="text-sub/40">·—</span>
            <div className="rounded-lg border border-tile bg-tile/50 px-2 py-1 text-center">
              <div className="text-sm font-semibold text-ink leading-none">{c.pct}%</div>
              <div className="text-[9px] text-sub tabular-nums">{c.num} / {c.den}</div>
            </div>
            {i === data.worstIdx && (
              <span className="rounded-full border border-alert/40 bg-alert/10 text-alert px-1.5 py-0.5 text-[9px] whitespace-nowrap">
                needs action
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function Card({
  title,
  sub,
  delay,
  children,
}: {
  title: string
  sub: string
  delay: number
  children: React.ReactNode
}) {
  return (
    <div className="rise rounded-xl border border-tile bg-surface p-4 min-w-0" style={{ animationDelay: `${delay}ms` }}>
      <div className="flex items-center gap-2 mb-0.5">
        <h2 className="text-sm font-semibold text-body">{title}</h2>
        <span className="ml-auto rounded-full border border-line bg-tile text-sub px-2 py-0.5 text-[10px]">
          insight
        </span>
      </div>
      <p className="text-[10px] text-sub/70 mb-3">{sub}</p>
      {children}
    </div>
  )
}

// reuse the insights engine's demand computation for the fourth card
import { api } from '../api'
import { computeInsights } from '../insights'

function DemandMini() {
  const [rows, setRows] = useState<{ label: string; value: number; display?: string }[]>([])
  useEffect(() => {
    Promise.all([api.leads(), api.appointments(), api.audit(50)])
      .then(([l, a, au]) => {
        const demand = computeInsights(l, a, au).insights.find((i) => i.id === 'demand_map')
        setRows((demand?.data ?? []).slice(0, 4))
      })
      .catch(() => {})
  }, [])
  const max = Math.max(...rows.map((r) => r.value), 1)
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2 text-[11px]" title={`${r.label}: ${r.display ?? r.value}`}>
          <span className="w-16 shrink-0 truncate text-sub">{r.label}</span>
          <div className="flex-1 h-2.5 rounded-[4px] bg-tile/60 overflow-hidden">
            <div className="h-full rounded-[4px]" style={{ width: `${(r.value / max) * 100}%`, background: '#0284c7' }} />
          </div>
          <span className="w-20 shrink-0 text-right text-sub/80 tabular-nums truncate">{r.display ?? r.value}</span>
        </div>
      ))}
    </div>
  )
}
