import { useEffect, useRef, useState } from 'react'
import { api, ChatSession, fmtDate } from '../api'
import { Markdown } from './Markdown'
import { toast } from './Toast'

interface Msg {
  role: string
  content: string
  created_at?: string
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
      <div className="shrink-0 px-4 py-2.5 border-b border-tile flex items-center gap-1">
        <div className="text-sm font-semibold mr-auto">
          Chat with your agent
          <div className="text-[11px] font-normal text-sub/80">Same agent as Discord</div>
        </div>
        <IconButton label="Previous chats" onClick={toggleHistory} active={historyOpen}>
          🕘
        </IconButton>
        <IconButton label="New chat" onClick={newChat}>＋</IconButton>
        <IconButton label="Clear this conversation" onClick={clearChat}>🗑</IconButton>
      </div>

      {historyOpen && (
        <div className="shrink-0 max-h-64 overflow-y-auto border-b border-tile bg-surface/70">
          {sessions.length === 0 && (
            <div className="px-4 py-3 text-xs text-sub/60">No previous conversations.</div>
          )}
          {sessions.map((s) => (
            <button
              key={s.session_id}
              onClick={() => openSession(s.session_id)}
              className={`block w-full text-left px-4 py-2.5 border-b border-tile/60 last:border-0
                          hover:bg-tile/60 transition-colors ${
                            s.session_id === sessionId ? 'bg-tile/40' : ''
                          }`}
            >
              <div className="flex items-center gap-2 text-xs">
                <span className="font-medium text-body truncate">
                  {s.preview || 'Empty conversation'}
                </span>
                {s.session_id === sessionId && (
                  <span className="shrink-0 text-[10px] text-accent">current</span>
                )}
              </div>
              <div className="text-[10px] text-sub/60 mt-0.5">
                {s.message_count} message{s.message_count === 1 ? '' : 's'} · {fmtDate(s.last_at)}
              </div>
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-3">
        {msgs.length === 0 && !thinking && (
          <div className="space-y-2">
            <div className="text-xs text-sub/60">Try one of the demo prompts:</div>
            {[
              'Add Minh Nguyen, 425-555-0198, buyer interested in Kirkland and Redmond',
              'Which active buyers need a follow-up?',
              'Show me everything we know about Sarah',
            ].map((p) => (
              <button
                key={p}
                onClick={() => send(p)}
                className="block w-full text-left rounded-lg border border-tile hover:border-line
                           px-3 py-2 text-xs text-sub hover:text-ink2 transition-colors"
              >
                {p}
              </button>
            ))}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`group flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
            <div
              className={`max-w-[85%] rounded-xl px-3 py-2 text-sm ${
                m.role === 'user'
                  ? 'bg-accent/15 text-ink2 whitespace-pre-wrap'
                  : 'bg-tile text-body'
              }`}
            >
              {m.role === 'user' ? m.content : <Markdown>{m.content}</Markdown>}
            </div>
            <div className="flex items-center gap-2 mt-0.5 px-1 opacity-0 group-hover:opacity-100 transition-opacity">
              {m.created_at && <span className="text-[10px] text-sub/60">{fmtDate(m.created_at)}</span>}
              <button
                onClick={() => copy(m.content)}
                className="text-[10px] text-sub/60 hover:text-body"
                title="Copy message"
              >
                copy
              </button>
            </div>
          </div>
        ))}
        {thinking && (
          <div className="bg-tile rounded-xl px-3 py-2 text-sm text-sub w-fit">
            <span className="animate-pulse">Agent is thinking…</span>
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="shrink-0 p-3 border-t border-tile bg-bg">
        <div className="flex items-center gap-2 rounded-xl border border-tile bg-surface pl-3 pr-1.5 py-1.5
                        focus-within:border-accent/60 transition-colors">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder="Which Bellevue buyers need a follow-up?"
            className="flex-1 bg-transparent text-sm placeholder:text-sub/50 focus:outline-none"
          />
          <button
            onClick={() => send()}
            disabled={thinking || !input.trim()}
            aria-label="Send message"
            className="h-8 w-8 shrink-0 rounded-lg bg-accent text-[#0b0f19] hover:brightness-110
                       disabled:opacity-40 disabled:hover:brightness-100 text-sm font-semibold"
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
        active ? 'bg-tile text-ink2' : 'text-sub/80 hover:text-ink2 hover:bg-tile/70'
      }`}
    >
      {children}
    </button>
  )
}
