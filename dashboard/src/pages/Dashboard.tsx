import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { FunnelData, fetchFunnel } from '../funnel'
import { computeInsights, Insight, Insights } from '../insights'
import { Skeleton } from '../components/Skeleton'
import { CameraIcon } from '../components/icons'

// The dashboard: sales funnel + deterministic insights on one page (home).
// Funnel geometry per the reference mock; marks single-hue; no gradients.
const MARK = '#0284c7'
const ALERT = '#f87171'

const SEVERITY: Record<Insight['severity'], { label: string; cls: string }> = {
  warn: { label: 'needs action', cls: 'bg-alert/10 text-alert border-alert/30' },
  good: { label: 'healthy', cls: 'bg-accent/10 text-accent border-accent/30' },
  info: { label: 'insight', cls: 'bg-tile text-sub border-line' },
}

// Keyed by date, not a boolean, and module-level (not per-mount): a dashboard
// left open overnight (the wall-mounted use case) must still POST the new
// day's row once the clock rolls over, AND navigating away from `/` and back
// (a remount, not a reload) must NOT re-POST today's row a second time — each
// POST writes an audit_log row that agentActivity() (insights.ts) counts, so
// a per-mount `let` here would self-inflate "agent actions in the last 24h"
// by one on every visit to `/` (two in StrictMode dev, which double-mounts).
let persistedDate = ''

export function DashboardPage() {
  const [fd, setFd] = useState<FunnelData | null>(null)
  const [ins, setIns] = useState<Insights | null>(null)

  useEffect(() => {
    let live = true
    const load = () =>
      Promise.all([fetchFunnel(true), api.audit(200)])
        .then(([funnel, audit]) => {
          if (!live) return
          setFd(funnel)
          // leads/appts already fetched inside fetchFunnel — reuse them
          // instead of issuing a second round-trip for the same data.
          const computed = computeInsights(funnel.leads, funnel.appts, audit)
          setIns(computed)
          if (computed.date !== persistedDate) {
            // daily write-through: K's morning-summary cron reads this back
            persistedDate = computed.date
            api.postInsights(computed).catch(() => {})
          }
        })
        .catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => {
      live = false
      clearInterval(t)
    }
  }, [])

  if (!fd || !ins)
    return (
      <div className="max-w-6xl space-y-4">
        <Skeleton className="h-80 w-full" />
        <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-44" />
          ))}
        </div>
      </div>
    )

  const byId = (id: string) => ins.insights.find((i) => i.id === id)
  const value = byId('pipeline_value')
  const aging = byId('aging')
  const booking = byId('booking_pattern')
  const activity = byId('agent_activity')

  return (
    <div className="max-w-6xl space-y-4">
      {/* sales funnel + summary */}
      <section className="rise rounded-xl border border-tile bg-surface p-5">
        <div className="flex items-center mb-5">
          <h1 className="text-lg font-semibold text-ink">Sales funnel</h1>
          <Link
            to="/scan"
            title="Scan a business card to add a lead"
            className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-line
                       hover:border-accent/60 px-3 py-1.5 text-xs text-body hover:text-accent
                       transition-colors"
          >
            <CameraIcon size={14} /> Scan card
          </Link>
        </div>
        <div className="grid lg:grid-cols-[1fr_260px] gap-6 items-start">
          <FunnelChart data={fd} />
          <aside className="space-y-4 text-sm">
            <div>
              <div className="text-xs text-sub">Overall conversion</div>
              <div className="text-3xl font-semibold text-accent mt-0.5">{fd.overallPct}%</div>
              <div className="text-xs text-sub mt-0.5">{fd.overallLabel}</div>
            </div>
            <div className="border-t border-tile pt-3">
              <div className="text-xs text-sub">Biggest bottleneck</div>
              <div className="font-semibold text-ink mt-0.5">{fd.bottleneck.label}</div>
              <p className="text-xs text-sub mt-1">{fd.bottleneck.detail}</p>
            </div>
            <div className="border-t border-tile pt-3">
              <div className="text-xs text-sub">Time to close</div>
              <div className="text-2xl font-semibold text-accent mt-0.5">
                {fd.avgDaysToClose != null
                  ? `${fd.avgDaysToClose} day${fd.avgDaysToClose === 1 ? '' : 's'}`
                  : '—'}
              </div>
              <div className="text-xs text-sub mt-0.5">avg from new lead to closed</div>
            </div>
          </aside>
        </div>
      </section>

      {/* insight cards — funnel content deduped (no donut: the funnel is above) */}
      <div className="grid md:grid-cols-2 xl:grid-cols-4 gap-3">
        {value && (
          <InsightCard insight={value} delay={40}>
            <StageSegments data={value.data} />
            {value.related && value.related.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {value.related.slice(0, 2).map((r) => (
                  <Link
                    key={r.lead_id}
                    to={`/lead/${r.lead_id}`}
                    className="rounded-full border border-line hover:border-accent/60 px-2.5 py-0.5
                               text-xs text-body hover:text-accent transition-colors"
                  >
                    {r.name} →
                  </Link>
                ))}
              </div>
            )}
          </InsightCard>
        )}
        {aging && (
          <InsightCard insight={aging} delay={80}>
            <SplitBar data={aging.data} />
          </InsightCard>
        )}
        <Card title="Top opportunities" delay={120}>
          <div className="space-y-2">
            {fd.opportunities.map((o) => (
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
        <Card title="Stage velocity" delay={160}>
          <div className="space-y-2">
            {fd.velocity.map((v) => (
              <div key={v.stage} className="flex items-center gap-2 text-xs">
                <span className="text-sub">{v.stage}</span>
                <span className="ml-auto tabular-nums text-body">{v.days != null ? `${v.days} days` : '—'}</span>
                {v.slow && (
                  <span className="rounded-full bg-alert/10 border border-alert/30 text-alert px-1.5 text-[10px]">
                    slow
                  </span>
                )}
              </div>
            ))}
          </div>
        </Card>

        <Card title="Lead source conversion" delay={200}>
          <BarList rows={fd.sources.map((s) => ({
            label: s.label,
            value: s.rate,
            display: `${s.rate}% (${s.won}/${s.total})`,
          }))} />
        </Card>
        <Card title="Demand by area" delay={240}>
          <BarList rows={(byId('demand_map')?.data ?? []).slice(0, 4)} />
        </Card>
        {booking && (
          <InsightCard insight={booking} delay={280}>
            <WeekBars data={booking.data} />
          </InsightCard>
        )}
        {activity && (
          <InsightCard insight={activity} delay={320}>
            <div className="space-y-1">
              {activity.data.slice(0, 3).map((d) => (
                <div key={d.label} className="flex items-center gap-2 text-xs">
                  <span className="truncate text-sub">{d.label}</span>
                  <span className="ml-auto tabular-nums text-body">×{d.value}</span>
                </div>
              ))}
              <Link to="/activity" className="inline-block text-xs text-accent hover:underline mt-1">
                Full stream →
              </Link>
            </div>
          </InsightCard>
        )}
      </div>

      {/* next best actions */}
      {fd.actions.length > 0 && (
        <section className="rise rounded-xl border border-tile bg-surface p-4" style={{ animationDelay: '360ms' }}>
          <div className="flex items-center gap-2 mb-3">
            <span className="text-accent">◎</span>
            <h2 className="text-sm font-semibold text-ink">Next best actions</h2>
          </div>
          <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
            {fd.actions.map((a) => (
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

/* ——— funnel chart (reference geometry: collinear sides, rounded corners) ——— */

const ROW_H = 54
const ROW_GAP = 8

function roundedTrap(x1t: number, x2t: number, x1b: number, x2b: number, y0: number, y1: number, r: number) {
  const dir = (ax: number, ay: number, bx: number, by: number) => {
    const dx = bx - ax
    const dy = by - ay
    const len = Math.hypot(dx, dy) || 1
    return { ux: dx / len, uy: dy / len }
  }
  const R = dir(x2t, y0, x2b, y1)
  const L = dir(x1b, y1, x1t, y0)
  return [
    `M ${x1t + r} ${y0}`,
    `L ${x2t - r} ${y0}`,
    `Q ${x2t} ${y0} ${x2t + R.ux * r} ${y0 + R.uy * r}`,
    `L ${x2b - R.ux * r} ${y1 - R.uy * r}`,
    `Q ${x2b} ${y1} ${x2b - r} ${y1}`,
    `L ${x1b + r} ${y1}`,
    `Q ${x1b} ${y1} ${x1b + L.ux * r} ${y1 + L.uy * r}`,
    `L ${x1t - L.ux * r} ${y0 - L.uy * r}`,
    `Q ${x1t} ${y0} ${x1t + r} ${y0}`,
    'Z',
  ].join(' ')
}

function FunnelChart({ data }: { data: FunnelData }) {
  const n = data.stages.length
  const height = n * ROW_H + (n - 1) * ROW_GAP
  const W_TOP = 96
  const W_BOTTOM = 46

  const wrapRef = useRef<HTMLDivElement>(null)
  const [pw, setPw] = useState(0)
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver((es) => setPw(es[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const widthAt = (y: number) => ((W_TOP - (W_TOP - W_BOTTOM) * (y / height)) / 100) * pw

  return (
    <div className="flex gap-4">
      <div ref={wrapRef} className="flex-1 relative min-w-0" style={{ height }}>
        {pw > 0 && (
          <svg
            width={pw}
            height={height}
            className="rise absolute inset-0 overflow-visible"
            style={{ filter: 'drop-shadow(0 0 12px rgba(14,165,233,0.22))' }}
          >
            {data.stages.map((s, i) => {
              const y0 = i * (ROW_H + ROW_GAP)
              const y1 = y0 + ROW_H
              const tw = widthAt(y0)
              const bw = widthAt(y1)
              const d = roundedTrap((pw - tw) / 2, (pw + tw) / 2, (pw - bw) / 2, (pw + bw) / 2, y0, y1, 9)
              return (
                <path key={s.key} d={d} className="funnel-path" fill="rgba(30,58,102,0.62)"
                      stroke="rgba(96,190,250,0.5)" strokeWidth="1">
                  <title>{`${s.label}: ${s.count}`}</title>
                </path>
              )
            })}
          </svg>
        )}
        {data.stages.map((s, i) => (
          <div
            key={s.key}
            className="absolute inset-x-0 flex flex-col items-center justify-center pointer-events-none"
            style={{ top: i * (ROW_H + ROW_GAP), height: ROW_H }}
          >
            <span className="text-[11px] text-ink2/85 leading-none">{s.label}</span>
            <span className="text-lg font-semibold text-ink leading-tight tabular-nums">{s.count}</span>
          </div>
        ))}
      </div>
      <div className="w-40 shrink-0 relative" style={{ height }}>
        {data.conversions.map((c, i) => (
          <div
            key={i}
            className="absolute left-0 flex items-center gap-1.5 -translate-y-1/2"
            style={{ top: (i + 1) * ROW_H + i * ROW_GAP + ROW_GAP / 2 }}
          >
            <span className="h-px w-3 bg-line" />
            <div className="rounded-lg border border-tile bg-tile/50 px-2 py-0.5 text-center">
              <div className="text-sm font-semibold text-ink leading-tight">{c.pct}%</div>
              <div className="text-[9px] text-sub tabular-nums leading-none pb-0.5">{c.num} / {c.den}</div>
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

/* ——— cards + small graphics (single validated hue; labels carry identity) ——— */

function Card({ title, delay, children }: { title: string; delay: number; children: React.ReactNode }) {
  return (
    <div className="rise rounded-xl border border-tile bg-surface p-4 min-w-0" style={{ animationDelay: `${delay}ms` }}>
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold text-body">{title}</h2>
        <span className="ml-auto rounded-full border border-line bg-tile text-sub px-2 py-0.5 text-[10px]">
          insight
        </span>
      </div>
      {children}
    </div>
  )
}

function InsightCard({ insight, delay, children }: { insight: Insight; delay: number; children: React.ReactNode }) {
  const sev = SEVERITY[insight.severity]
  return (
    <div
      className="rise rounded-xl border border-tile bg-surface p-4 min-w-0"
      style={{ animationDelay: `${delay}ms` }}
      title={insight.detail}
    >
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-body truncate">{insight.title}</h2>
        <span className={`ml-auto shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${sev.cls}`}>
          {sev.label}
        </span>
      </div>
      <div className="text-[15px] font-semibold text-ink mt-1.5 mb-2 leading-snug line-clamp-2">
        {insight.headline}
      </div>
      {children}
    </div>
  )
}

function BarList({ rows }: { rows: { label: string; value: number; display?: string }[] }) {
  const max = Math.max(...rows.map((r) => r.value), 1)
  return (
    <div className="space-y-1.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2 text-[11px]" title={`${r.label}: ${r.display ?? r.value}`}>
          <span className="w-16 shrink-0 truncate text-sub capitalize">{r.label}</span>
          <div className="flex-1 h-2.5 rounded-[4px] bg-tile/60 overflow-hidden">
            <div
              className="h-full rounded-[4px]"
              style={{ width: `${Math.max((r.value / max) * 100, r.value > 0 ? 5 : 0)}%`, background: MARK }}
            />
          </div>
          <span className="w-20 shrink-0 text-right text-sub/80 tabular-nums truncate">{r.display ?? r.value}</span>
        </div>
      ))}
    </div>
  )
}

function SplitBar({ data }: { data: Insight['data'] }) {
  const fresh = data.find((d) => d.label.startsWith('Touched'))?.value ?? 0
  const stale = data.find((d) => d.label.startsWith('Idle'))?.value ?? 0
  const total = Math.max(fresh + stale, 1)
  return (
    <div>
      <div className="flex h-4 rounded-[4px] overflow-hidden gap-[2px]">
        <div style={{ width: `${(fresh / total) * 100}%`, background: MARK }} title={`Touched < 2d: ${fresh}`} />
        <div style={{ width: `${(stale / total) * 100}%`, background: ALERT }} title={`Idle 2+ days: ${stale}`} />
      </div>
      <div className="flex justify-between mt-2 text-[11px]">
        <span className="text-sub"><span style={{ color: MARK }}>●</span> touched &lt;2d · {fresh}</span>
        <span className="text-sub"><span style={{ color: ALERT }}>●</span> idle 2d+ · {stale}</span>
      </div>
    </div>
  )
}

function WeekBars({ data }: { data: Insight['data'] }) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const counts = new Map(data.map((d) => [d.label, d.value]))
  const max = Math.max(...days.map((d) => counts.get(d) ?? 0), 1)
  return (
    <div className="flex items-end gap-1.5 h-14">
      {days.map((d) => {
        const v = counts.get(d) ?? 0
        return (
          <div key={d} className="flex-1 flex flex-col items-center gap-1 min-w-0" title={`${d}: ${v}`}>
            <div
              className="w-full rounded-t-[3px]"
              style={{ height: `${Math.max((v / max) * 38, v > 0 ? 6 : 2)}px`, background: v > 0 ? MARK : '#374151' }}
            />
            <span className="text-[9px] text-sub/60">{d[0]}</span>
          </div>
        )
      })}
    </div>
  )
}

function StageSegments({ data }: { data: Insight['data'] }) {
  const total = Math.max(data.reduce((t, d) => t + d.value, 0), 1)
  return (
    <div>
      <div className="flex h-4 rounded-[4px] overflow-hidden gap-[2px]">
        {data.map((d) => (
          <div
            key={d.label}
            title={`${d.label}: ${d.display ?? d.value}`}
            style={{ width: `${Math.max((d.value / total) * 100, d.value > 0 ? 3 : 0)}%`, background: MARK }}
            className="first:opacity-100 [&:nth-child(2)]:opacity-75 [&:nth-child(3)]:opacity-50"
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 mt-1.5 text-[10px] text-sub">
        {data.map((d) => (
          <span key={d.label} className="capitalize">
            {d.label}: <span className="text-body tabular-nums">{d.display ?? d.value}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
