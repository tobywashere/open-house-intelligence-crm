import { useEffect, useState } from 'react'
import { Link, Route, Routes } from 'react-router-dom'
import { api, Metrics } from './api'
import { AuditLog } from './components/AuditLog'
import { ChatPanel } from './components/ChatPanel'
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

  useEffect(() => {
    const load = () => api.metrics().then(setMetrics).catch(() => {})
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-zinc-800 px-6 py-3 flex items-center gap-4">
        <Link to="/" className="text-lg font-semibold tracking-tight">
          Open House <span className="text-emerald-400">Intelligence</span>
        </Link>
        <nav className="flex gap-3 text-sm text-zinc-400">
          <Link to="/" className="hover:text-zinc-100">Insights</Link>
          <Link to="/briefing" className="hover:text-zinc-100">Briefing</Link>
          <Link to="/leads" className="hover:text-zinc-100">Leads</Link>
          <Link to="/activity" className="hover:text-zinc-100">Agent activity</Link>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <LocalBadge metrics={metrics} />
          <DemoControls />
        </div>
      </header>
      <Toasts />

      <ReminderBanner />

      {metrics && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-px bg-zinc-800 border-b border-zinc-800">
          <Tile label="Active leads" value={metrics.active_leads} />
          <Tile label="High priority" value={metrics.high_priority} accent />
          <Tile label="Follow-ups due" value={metrics.followups_due} />
          <Tile label="Appointments" value={metrics.appointments_booked} />
          <Tile label="Avg response" value={`${metrics.avg_response_minutes}m`} />
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <main className="flex-1 overflow-y-auto p-6">
          <Routes>
            <Route path="/" element={<InsightsPage />} />
            <Route path="/briefing" element={<BriefingPage />} />
            <Route path="/leads" element={<Inbox />} />
            <Route path="/lead/:id" element={<LeadPage />} />
            <Route path="/activity" element={<AuditLog full />} />
          </Routes>
        </main>
        <aside className="w-96 border-l border-zinc-800 hidden lg:flex flex-col">
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
    <div className="bg-zinc-950 px-4 py-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className={`text-xl font-semibold ${accent ? 'text-emerald-400' : ''}`}>{value}</div>
    </div>
  )
}
