import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ChatSession, fmtDate } from '../api'
import { toast } from './Toast'

interface Msg {
  role: string
  content: string
  created_at?: string
}

// Agent replies may contain [Name](lead:12) — render those as profile links.
// (Syntax documented in docs/BRIEFING-UI.md; K's prompts emit it.)
function renderWithLinks(text: string) {
  const re = /\[([^\]]+)\]\(lead:(\d+)\)/g
  const parts: (string | JSX.Element)[] = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    parts.push(
      <Link key={m.index} to={`/lead/${m[2]}`} className="text-emerald-400 underline hover:text-emerald-300">
        {m[1]}
      </Link>,
    )
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

const SESSION_KEY = 'ohi-chat-session'
const newSessionId = () => `dash-${Date.now().toString(36)}`

export function ChatPanel() {
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(SESSION_KEY) ?? 'dashboard')
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    localStorage.setItem(SESSION_KEY, sessionId)
    api.chatHistory(sessionId).then(setMsgs).catch(() => setMsgs([]))
  }, [sessionId])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, thinking])

  const refreshSessions = () =>
    api.chatSessions().then(setSessions).catch(() => setSessions([]))

  const toggleHistory = () => {
    if (!historyOpen) refreshSessions()
    setHistoryOpen(!historyOpen)
  }

  const newChat = () => {
    setHistoryOpen(false)
    setSessionId(newSessionId())
  }

  const clearChat = async () => {
    try {
      await api.clearChat(sessionId)
      setMsgs([])
      toast('Conversation cleared')
    } catch {
      toast('⚠ Could not clear — is the backend running?')
    }
  }

  const openSession = (id: string) => {
    setHistoryOpen(false)
    setSessionId(id)
  }

  const send = async (preset?: string) => {
    const message = (preset ?? input).trim()
    if (!message || thinking) return
    setInput('')
    setMsgs((m) => [...m, { role: 'user', content: message }])
    setThinking(true)
    try {
      const { reply } = await api.chat(message, sessionId)
      setMsgs((m) => [...m, { role: 'agent', content: reply }])
    } catch {
      setMsgs((m) => [...m, { role: 'agent', content: '⚠ Could not reach the agent.' }])
    } finally {
      setThinking(false)
    }
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).then(() => toast('Copied'))
  }

  return (
    <>
      <div className="shrink-0 px-4 py-2.5 border-b border-zinc-800/80 flex items-center gap-1">
        <div className="text-sm font-semibold mr-auto">
          Chat with your agent
          <div className="text-[11px] font-normal text-zinc-500">Same agent as Discord</div>
        </div>
        <IconButton label="Previous chats" onClick={toggleHistory} active={historyOpen}>
          🕘
        </IconButton>
        <IconButton label="New chat" onClick={newChat}>＋</IconButton>
        <IconButton label="Clear this conversation" onClick={clearChat}>🗑</IconButton>
      </div>

      {historyOpen && (
        <div className="shrink-0 max-h-64 overflow-y-auto border-b border-zinc-800/80 bg-zinc-900/40">
          {sessions.length === 0 && (
            <div className="px-4 py-3 text-xs text-zinc-600">No previous conversations.</div>
          )}
          {sessions.map((s) => (
            <button
              key={s.session_id}
              onClick={() => openSession(s.session_id)}
              className={`block w-full text-left px-4 py-2.5 border-b border-zinc-800/40 last:border-0
                          hover:bg-zinc-800/40 transition-colors ${
                            s.session_id === sessionId ? 'bg-zinc-800/30' : ''
                          }`}
            >
              <div className="flex items-center gap-2 text-xs">
                <span className="font-medium text-zinc-300 truncate">
                  {s.preview || 'Empty conversation'}
                </span>
                {s.session_id === sessionId && (
                  <span className="shrink-0 text-[10px] text-emerald-400">current</span>
                )}
              </div>
              <div className="text-[10px] text-zinc-600 mt-0.5">
                {s.message_count} message{s.message_count === 1 ? '' : 's'} · {fmtDate(s.last_at)}
              </div>
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {msgs.length === 0 && !thinking && (
          <div className="space-y-2">
            <div className="text-xs text-zinc-600">Try one of the demo prompts:</div>
            {[
              'Add Minh Nguyen, 425-555-0198, buyer interested in Kirkland and Redmond',
              'Which active buyers need a follow-up?',
              'Show me everything we know about Sarah',
            ].map((p) => (
              <button
                key={p}
                onClick={() => send(p)}
                className="block w-full text-left rounded-lg border border-zinc-800 hover:border-zinc-600
                           px-3 py-2 text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
              >
                {p}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`group flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
            <div
              className={`max-w-[85%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap ${
                m.role === 'user'
                  ? 'bg-emerald-600/20 text-emerald-100'
                  : 'bg-zinc-800/80 text-zinc-200'
              }`}
            >
              {renderWithLinks(m.content)}
            </div>
            <div className="flex items-center gap-2 mt-0.5 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
              {m.created_at && <span className="text-[10px] text-zinc-600">{fmtDate(m.created_at)}</span>}
              <button
                onClick={() => copy(m.content)}
                className="text-[10px] text-zinc-600 hover:text-zinc-300"
                title="Copy message"
              >
                copy
              </button>
            </div>
          </div>
        ))}
        {thinking && (
          <div className="bg-zinc-800/80 rounded-xl px-3 py-2 text-sm text-zinc-400 w-fit">
            <span className="animate-pulse">Agent is thinking…</span>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="shrink-0 p-3 border-t border-zinc-800/80 bg-zinc-950">
        <div className="flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900 pl-3 pr-1.5 py-1.5
                        focus-within:border-emerald-500/60 transition-colors">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder="Which Bellevue buyers need a follow-up?"
            className="flex-1 bg-transparent text-sm placeholder:text-zinc-600 focus:outline-none"
          />
          <button
            onClick={() => send()}
            disabled={thinking || !input.trim()}
            aria-label="Send message"
            className="h-8 w-8 shrink-0 rounded-lg bg-emerald-600 hover:bg-emerald-500
                       disabled:opacity-40 disabled:hover:bg-emerald-600 text-sm font-semibold"
          >
            ↑
          </button>
        </div>
      </div>
    </>
  )
}

function IconButton({
  label,
  onClick,
  active,
  children,
}: {
  label: string
  onClick: () => void
  active?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className={`h-7 w-7 rounded-md text-sm transition-colors ${
        active ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/60'
      }`}
    >
      {children}
    </button>
  )
}
