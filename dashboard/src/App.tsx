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
import { fetchFunnel, Kpi } from './funnel'
import { DashboardPage } from './pages/Dashboard'
import { Inbox } from './pages/Inbox'
import { LeadPage } from './pages/Lead'

const CHAT_W_KEY = 'ohi-chat-width'
const clampChatW = (w: number) =>
  Math.min(Math.max(w, 320), Math.min(860, Math.round(window.innerWidth * 0.7)))

export default function App() {
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  // resizable chat rail — drag the divider between main content and chat
  const [chatW, setChatW] = useState(() => clampChatW(Number(localStorage.getItem(CHAT_W_KEY)) || 384))
  const startChatResize = (e: React.PointerEvent) => {
    e.preventDefault()
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    const onMove = (ev: PointerEvent) => setChatW(clampChatW(window.innerWidth - ev.clientX))
    const onUp = () => {
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      setChatW((w) => {
        localStorage.setItem(CHAT_W_KEY, String(w))
        return w
      })
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }
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

  // KPI strip: six funnel KPIs, ▲/▼ deltas only when a real yesterday snapshot exists
  const [kpis, setKpis] = useState<Kpi[] | null>(null)
  useEffect(() => {
    const load = () => fetchFunnel().then((d) => setKpis(d.kpis)).catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => clearInterval(t)
  }, [])

  const navCls = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2.5 py-1 transition-colors ${
      isActive ? 'bg-tile text-ink2' : 'text-sub/80 hover:text-ink2'
    }`

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <header className="shrink-0 border-b border-tile bg-bg/90 backdrop-blur px-6 py-2.5 flex items-center gap-5">
        <Link to="/" className="text-[17px] font-semibold tracking-tight text-ink">
          Open House <span className="brand-gradient">Intelligence</span>
        </Link>
        <nav className="flex gap-1 text-sm">
          <NavLink to="/leads" className={navCls}>Leads</NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setSummaryOpen(true)}
            className="rounded-full border border-line hover:border-accent/60 px-3 py-1.5
                       text-xs text-body hover:text-accent transition-colors"
          >
            ☀️ Daily summary
          </button>
          <LocalBadge metrics={metrics} />
          {/* dev-only: raw agent/tool audit stream, deliberately not a nav item */}
          <NavLink
            to="/activity"
            title="Agent activity (dev)"
            className={({ isActive }) =>
              `h-8 w-8 rounded-full border flex items-center justify-center text-xs transition-colors ${
                isActive
                  ? 'border-accent/60 text-accent'
                  : 'border-line text-sub/70 hover:text-accent hover:border-accent/60'
              }`
            }
          >
            {'</>'}
          </NavLink>
          <DemoControls />
        </div>
      </header>
      <Toasts />

      <ReminderBanner />

      {kpis && (
        <div className="shrink-0 grid grid-cols-3 sm:grid-cols-6 border-b border-tile bg-surface/40 divide-x divide-tile/60">
          {kpis.map((k, i) => (
            <Tile key={k.label} kpi={k} accent={i === 5} />
          ))}
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <main className="flex-1 overflow-y-auto p-6 min-w-0">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/leads" element={<Inbox />} />
            <Route path="/lead/:id" element={<LeadPage />} />
            <Route path="/activity" element={<AuditLog full />} />
          </Routes>
        </main>
        {/* drag handle: widen/narrow the chat rail (double-click resets) */}
        <div
          onPointerDown={startChatResize}
          onDoubleClick={() => {
            setChatW(384)
            localStorage.setItem(CHAT_W_KEY, '384')
          }}
          title="Drag to resize chat"
          className="hidden lg:block w-1.5 -mr-1.5 shrink-0 cursor-col-resize z-10
                     hover:bg-accent/50 active:bg-accent/70 transition-colors"
        />
        {/* chat rail is viewport-fixed: the input is always visible, only messages scroll */}
        <aside
          style={{ width: chatW }}
          className="shrink-0 border-l border-tile hidden lg:flex flex-col min-h-0 bg-bg"
        >
          <ChatPanel />
        </aside>
      </div>

      {/* small screens: chat becomes a floating button + full-screen overlay */}
      {!chatOpen && (
        <button
          onClick={() => setChatOpen(true)}
          className="lg:hidden fixed bottom-4 right-4 z-40 h-12 w-12 rounded-full bg-accent
                     text-[#0b0f19] hover:brightness-110 shadow-xl text-lg"
          title="Chat with your agent"
        >
          💬
        </button>
      )}
      {summaryOpen && <DailySummaryOverlay onClose={closeSummary} />}

      {chatOpen && (
        <div className="lg:hidden fixed inset-0 z-50 bg-bg flex flex-col">
          <div className="flex justify-end border-b border-tile px-2 py-1.5">
            <button
              onClick={() => setChatOpen(false)}
              className="text-sm text-sub hover:text-ink px-2 py-1"
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

function Tile({ kpi, accent }: { kpi: Kpi; accent?: boolean }) {
  return (
    <div className="px-4 py-2">
      <div className="text-[11px] uppercase tracking-wider text-sub/60 truncate">{kpi.label}</div>
      <div className={`text-lg font-semibold tabular-nums leading-tight ${accent ? 'text-accent' : 'text-ink'}`}>
        {kpi.value}
      </div>
      {kpi.delta && (
        <div className={`text-[10px] ${kpi.up === true ? 'text-accent' : kpi.up === false ? 'text-alert' : 'text-sub/60'}`}>
          {kpi.delta}
        </div>
      )}
    </div>
  )
}
