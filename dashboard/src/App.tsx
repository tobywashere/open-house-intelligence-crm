import { useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes } from 'react-router-dom'
import { api, Metrics } from './api'
import { AuditLog } from './components/AuditLog'
import { ChatPanel } from './components/ChatPanel'
import { DailySummaryOverlay } from './components/DailySummaryOverlay'
import { DemoControls } from './components/DemoControls'
import { LocalBadge } from './components/LocalBadge'
import { ReminderBanner } from './components/ReminderBanner'
import { Toasts } from './components/Toast'
import { BriefingPage } from './pages/Briefing'
import { Inbox } from './pages/Inbox'
import { InsightsPage } from './pages/Insights'
import { LeadPage } from './pages/Lead'

export default function App() {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  // auto-open the daily summary once per day; reopenable any time from the header
  const [summaryOpen, setSummaryOpen] = useState(() => {
    const today = new Date().toISOString().slice(0, 10)
    return localStorage.getItem('ohi-summary-seen') !== today
  })
  const closeSummary = () => {
    localStorage.setItem('ohi-summary-seen', new Date().toISOString().slice(0, 10))
    setSummaryOpen(false)
  }

  useEffect(() => {
    const load = () => api.metrics().then(setMetrics).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const navCls = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2.5 py-1 transition-colors ${
      isActive ? 'bg-zinc-800/70 text-zinc-100' : 'text-zinc-500 hover:text-zinc-200'
    }`

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="shrink-0 border-b border-zinc-800/80 bg-zinc-950/90 backdrop-blur px-6 py-2.5 flex items-center gap-5">
        <Link to="/" className="text-[17px] font-semibold tracking-tight">
          Open House <span className="text-emerald-400">Intelligence</span>
        </Link>
        <nav className="flex gap-1 text-sm">
          <NavLink to="/" end className={navCls}>Insights</NavLink>
          <NavLink to="/briefing" className={navCls}>Briefing</NavLink>
          <NavLink to="/leads" className={navCls}>Leads</NavLink>
          <NavLink to="/activity" className={navCls}>Agent activity</NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setSummaryOpen(true)}
            className="rounded-full border border-zinc-700 hover:border-emerald-500/60 px-3 py-1.5
                       text-xs text-zinc-300 hover:text-emerald-300 transition-colors"
          >
            ☀️ Daily summary
          </button>
          <LocalBadge metrics={metrics} />
          <DemoControls />
        </div>
      </header>
      <Toasts />

      <ReminderBanner />

      {metrics && (
        <div className="shrink-0 grid grid-cols-2 sm:grid-cols-5 border-b border-zinc-800/60 bg-zinc-900/20 divide-x divide-zinc-800/40">
          <Tile label="Active leads" value={metrics.active_leads} />
          <Tile label="High priority" value={metrics.high_priority} accent />
          <Tile label="Follow-ups due" value={metrics.followups_due} />
          <Tile label="Appointments" value={metrics.appointments_booked} />
          <Tile label="Avg response" value={`${metrics.avg_response_minutes}m`} />
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <main className="flex-1 overflow-y-auto p-6 min-w-0">
          <Routes>
            <Route path="/" element={<InsightsPage />} />
            <Route path="/briefing" element={<BriefingPage />} />
            <Route path="/leads" element={<Inbox />} />
            <Route path="/lead/:id" element={<LeadPage />} />
            <Route path="/activity" element={<AuditLog full />} />
          </Routes>
        </main>
        {/* chat rail is viewport-fixed: the input is always visible, only messages scroll */}
        <aside className="w-96 shrink-0 border-l border-zinc-800 hidden lg:flex flex-col min-h-0 bg-zinc-950">
          <ChatPanel />
        </aside>
      </div>

      {/* small screens: chat becomes a floating button + full-screen overlay */}
      {!chatOpen && (
        <button
          onClick={() => setChatOpen(true)}
          className="lg:hidden fixed bottom-4 right-4 z-40 h-12 w-12 rounded-full bg-emerald-600
                     hover:bg-emerald-500 shadow-xl text-lg"
          title="Chat with your agent"
        >
          💬
        </button>
      )}
      {summaryOpen && <DailySummaryOverlay onClose={closeSummary} />}

      {chatOpen && (
        <div className="lg:hidden fixed inset-0 z-50 bg-zinc-950 flex flex-col">
          <div className="flex justify-end border-b border-zinc-800 px-2 py-1.5">
            <button
              onClick={() => setChatOpen(false)}
              className="text-sm text-zinc-400 hover:text-zinc-100 px-2 py-1"
            >
              ✕ Close
            </button>
          </div>
          <div className="flex-1 flex flex-col min-h-0">
            <ChatPanel />
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, accent }: { label: string; value: number | string; accent?: boolean }) {
  return (
    <div className="px-4 py-2.5">
      <div className="text-[11px] uppercase tracking-wider text-zinc-600">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${accent ? 'text-emerald-400' : ''}`}>{value}</div>
    </div>
  )
}
