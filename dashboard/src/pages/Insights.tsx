import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { computeInsights, Insight, Insights } from '../insights'
import { Skeleton } from '../components/Skeleton'

// Chart mark colors — validated against the deck surface (dataviz six checks):
// #0284c7 passes lightness band + 3:1 contrast; #f87171 is status (with labels),
// indigo is decoration-only and never used as a data mark next to sky.
const MARK = '#0284c7'
const TRACK = '#374151'
const ALERT = '#f87171'

const SEVERITY: Record<Insight['severity'], { label: string; cls: string }> = {
  warn: { label: 'needs action', cls: 'bg-alert/10 text-alert border-alert/30' },
  good: { label: 'healthy', cls: 'bg-accent/10 text-accent border-accent/30' },
  info: { label: 'insight', cls: 'bg-tile text-sub border-line' },
}

export function InsightsPage() {
  const [result, setResult] = useState<Insights | null>(null)

  useEffect(() => {
    let persisted = false
    const load = () =>
      Promise.all([api.leads(), api.appointments(), api.audit(200)])
        .then(([leads, appts, audit]) => {
          const computed = computeInsights(leads, appts, audit)
          setResult(computed)
          if (!persisted) {
            // write-through (once per visit): persists today's payload so K's
            // morning-summary cron can GET /api/insights — docs/INSIGHTS.md Phase 2
            persisted = true
            api.postInsights(computed).catch(() => {})
          }
        })
        .catch(() => {})
    load()
    const t = setInterval(load, 15_000)
    return () => clearInterval(t)
  }, [])

  if (!result)
    return (
      <div className="h-full grid gap-3 md:grid-cols-2 xl:grid-cols-4 xl:grid-rows-2">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} className={i === 0 ? 'xl:col-span-2 h-full min-h-40' : 'h-full min-h-40'} />
        ))}
      </div>
    )

  const byId = (id: string) => result.insights.find((i) => i.id === id)
  const value = byId('pipeline_value')
  const funnel = byId('funnel')
  const aging = byId('aging')
  const source = byId('source_effectiveness')
  const demand = byId('demand_map')
  const booking = byId('booking_pattern')
  const activity = byId('agent_activity')

  // one viewport, no scrolling (xl+): 4×2 bento, hero spans two columns
  return (
    <div className="h-full min-h-0 grid gap-3 md:grid-cols-2 xl:grid-cols-4 xl:grid-rows-2 xl:overflow-hidden overflow-y-auto">
      {value && (
        <Tile insight={value} className="xl:col-span-2" delay={0}>
          <div className="flex-1 flex flex-col justify-end gap-3">
            <StageSegments data={value.data} />
            {value.related && value.related.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {value.related.slice(0, 3).map((r) => (
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
          </div>
        </Tile>
      )}
      {funnel && (
        <Tile insight={funnel} delay={60}>
          <Donut data={funnel.data} />
        </Tile>
      )}
      {aging && (
        <Tile insight={aging} delay={120}>
          <SplitBar data={aging.data} />
        </Tile>
      )}
      {source && (
        <Tile insight={source} delay={180}>
          <MiniBars data={source.data.slice(0, 4)} />
        </Tile>
      )}
      {demand && (
        <Tile insight={demand} delay={240}>
          <MiniBars data={demand.data.slice(0, 4)} />
        </Tile>
      )}
      {booking && (
        <Tile insight={booking} delay={300}>
          <WeekBars data={booking.data} />
        </Tile>
      )}
      {activity && (
        <Tile insight={activity} delay={360}>
          <div className="flex-1 flex flex-col justify-end gap-1">
            {activity.data.slice(0, 3).map((d) => (
              <div key={d.label} className="flex items-center gap-2 text-xs">
                <span className="truncate text-sub">{d.label}</span>
                <span className="ml-auto tabular-nums text-body">×{d.value}</span>
              </div>
            ))}
            <Link to="/activity" className="text-xs text-accent hover:underline mt-1">
              Full stream →
            </Link>
          </div>
        </Tile>
      )}
    </div>
  )
}

function Tile({
  insight,
  className = '',
  delay,
  children,
}: {
  insight: Insight
  className?: string
  delay: number
  children: React.ReactNode
}) {
  const sev = SEVERITY[insight.severity]
  return (
    <div
      className={`rise min-h-0 flex flex-col rounded-xl border border-tile bg-surface p-4 ${className}`}
      style={{ animationDelay: `${delay}ms` }}
      title={insight.detail}
    >
      <div className="flex items-center gap-2">
        <h2 className="text-xs font-semibold text-sub uppercase tracking-wider truncate">{insight.title}</h2>
        <span className={`ml-auto shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${sev.cls}`}>
          {sev.label}
        </span>
      </div>
      <div className="text-[15px] xl:text-base font-semibold text-ink mt-1.5 leading-snug line-clamp-2">
        {insight.headline}
      </div>
      <div className="flex-1 min-h-0 flex flex-col justify-end mt-2">{children}</div>
    </div>
  )
}

/* ——— tile graphics: one validated mark hue; identity lives in labels ——— */

function MiniBars({ data }: { data: Insight['data'] }) {
  const max = Math.max(...data.map((d) => d.value), 1)
  return (
    <div className="space-y-1.5">
      {data.map((d) => (
        <div key={d.label} className="flex items-center gap-2 text-[11px]" title={`${d.label}: ${d.display ?? d.value}`}>
          <span className="w-16 shrink-0 truncate text-sub capitalize">{d.label}</span>
          <div className="flex-1 h-2.5 rounded-[4px] bg-tile/60 overflow-hidden">
            <div
              className="h-full rounded-[4px] transition-[width] duration-300"
              style={{ width: `${Math.max((d.value / max) * 100, d.value > 0 ? 4 : 0)}%`, background: MARK }}
            />
          </div>
          <span className="w-20 shrink-0 text-right text-sub/80 tabular-nums truncate">
            {d.display ?? d.value}
          </span>
        </div>
      ))}
    </div>
  )
}

// booked share of the funnel — accent arc on a neutral track, number in the middle
function Donut({ data }: { data: Insight['data'] }) {
  const get = (label: string) => data.find((d) => d.label === label)?.value ?? 0
  const total = data.reduce((t, d) => t + d.value, 0)
  const booked = get('Meeting booked') + get('Closed')
  const pct = total ? booked / total : 0
  const r = 34
  const c = 2 * Math.PI * r
  return (
    <div className="flex items-center gap-4">
      <svg width="92" height="92" viewBox="0 0 92 92" className="-rotate-90 shrink-0">
        <circle cx="46" cy="46" r={r} fill="none" stroke={TRACK} strokeWidth="11" />
        <circle
          cx="46" cy="46" r={r} fill="none"
          stroke={MARK} strokeWidth="11" strokeLinecap="round"
          strokeDasharray={`${pct * c} ${c}`}
        />
        <text
          x="46" y="46" transform="rotate(90 46 46)"
          textAnchor="middle" dominantBaseline="central"
          fill="#ffffff" fontSize="17" fontWeight="700"
        >
          {Math.round(pct * 100)}%
        </text>
      </svg>
      <div className="space-y-1 text-[11px] min-w-0">
        {data.map((d) => (
          <div key={d.label} className="flex gap-2">
            <span className="text-sub truncate">{d.label}</span>
            <span className="ml-auto tabular-nums text-body">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// fresh vs going-stale — status pair, always labeled (never color alone)
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
        <span className="text-sub">
          <span style={{ color: MARK }}>●</span> touched &lt;2d · {fresh}
        </span>
        <span className="text-sub">
          <span style={{ color: ALERT }}>●</span> idle 2d+ · {stale}
        </span>
      </div>
    </div>
  )
}

// tours by weekday — one hue, labeled columns
function WeekBars({ data }: { data: Insight['data'] }) {
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
  const counts = new Map(data.map((d) => [d.label, d.value]))
  const max = Math.max(...days.map((d) => counts.get(d) ?? 0), 1)
  return (
    <div className="flex items-end gap-1.5 h-16">
      {days.map((d) => {
        const v = counts.get(d) ?? 0
        return (
          <div key={d} className="flex-1 flex flex-col items-center gap-1 min-w-0" title={`${d}: ${v}`}>
            <div className="w-full rounded-t-[3px]" style={{
              height: `${Math.max((v / max) * 44, v > 0 ? 6 : 2)}px`,
              background: v > 0 ? MARK : TRACK,
            }} />
            <span className="text-[9px] text-sub/60">{d[0]}</span>
          </div>
        )
      })}
    </div>
  )
}

// hero tile: pipeline $ by stage as gapped segments of one hue
function StageSegments({ data }: { data: Insight['data'] }) {
  const total = Math.max(data.reduce((t, d) => t + d.value, 0), 1)
  return (
    <div>
      <div className="flex h-5 rounded-[4px] overflow-hidden gap-[2px]">
        {data.map((d) => (
          <div
            key={d.label}
            title={`${d.label}: ${d.display ?? d.value}`}
            style={{ width: `${Math.max((d.value / total) * 100, d.value > 0 ? 3 : 0)}%`, background: MARK }}
            className="first:opacity-100 [&:nth-child(2)]:opacity-75 [&:nth-child(3)]:opacity-50"
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-0.5 mt-2 text-[11px] text-sub">
        {data.map((d) => (
          <span key={d.label} className="capitalize">
            {d.label}: <span className="text-body tabular-nums">{d.display ?? d.value}</span>
          </span>
        ))}
      </div>
    </div>
  )
}
