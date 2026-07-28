import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
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
  const { pathname } = useLocation()
  const placeholder =
    pathname === '/'
      ? 'Ask anything about your pipeline…'
      : pathname.startsWith('/lead/')
        ? 'Ask about this client…'
        : 'Ask anything about your leads'
  const [sessionId, setSessionId] = useState(() => localStorage.getItem(SESSION_KEY) ?? 'dashboard')
  const sessionIdRef = useRef(sessionId)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [historyOpen, setHistoryOpen] = useState(false)
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    sessionIdRef.current = sessionId
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
    const issued = sessionId
    setInput('')
    setMsgs((m) => [...m, { role: 'user', content: message }])
    setThinking(true)
    try {
      const { reply } = await api.chat(message, issued)
      // The user may have switched/started a new chat while this request was
      // in flight — don't splice a reply for a stale session into the new one.
      if (issued !== sessionIdRef.current) return
      setMsgs((m) => [...m, { role: 'agent', content: reply }])
    } catch {
      if (issued !== sessionIdRef.current) return
      setMsgs((m) => [...m, { role: 'agent', content: '⚠ Could not reach the agent.' }])
    } finally {
      if (issued === sessionIdRef.current) setThinking(false)
    }
  }

  const copy = (text: string, idx: number) => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopiedIdx(idx)
      setTimeout(() => setCopiedIdx((c) => (c === idx ? null : c)), 1500)
    })
  }

  return (
    <>
      <div className="shrink-0 px-3 py-2 border-b border-tile flex items-center gap-0.5">
        <div className="text-sm font-semibold mr-auto pl-1">
          Chat with your agent
          <div className="text-[11px] font-normal text-sub/80">Same agent as Discord</div>
        </div>
        <IconButton label="Chat history" onClick={toggleHistory} active={historyOpen}>
          <ClockIcon />
        </IconButton>
        <IconButton label="New chat" onClick={newChat}>
          <NewChatIcon />
        </IconButton>
        <IconButton label="Delete conversation" onClick={clearChat}>
          <TrashIcon />
        </IconButton>
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
              className={`flex w-full items-start gap-2.5 text-left px-3 py-2.5 border-b border-tile/60
                          last:border-0 hover:bg-tile/60 transition-colors ${
                            s.session_id === sessionId ? 'bg-tile/40' : ''
                          }`}
            >
              <span className="mt-0.5 shrink-0 text-sub/70">
                <BubbleIcon />
              </span>
              <span className="min-w-0">
                <span className="flex items-center gap-2 text-xs">
                  <span className="font-medium text-body truncate">
                    {s.preview || 'Empty conversation'}
                  </span>
                  {s.session_id === sessionId && (
                    <span className="shrink-0 text-[10px] text-accent">current</span>
                  )}
                </span>
                <span className="block text-[10px] text-sub/60 mt-0.5">
                  {s.message_count} message{s.message_count === 1 ? '' : 's'} · {fmtDate(s.last_at)}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto px-4 py-4 space-y-4">
        {msgs.length === 0 && !thinking && (
          <div className="space-y-2">
            <div className="text-xs text-sub/60">Try:</div>
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
        {msgs.map((m, i) =>
          m.role === 'user' ? (
            // user turn: right-aligned bubble, like ChatGPT
            <div key={i} className="group flex flex-col items-end">
              <div className="max-w-[85%] rounded-xl rounded-br-md bg-tile px-4 py-2 text-sm text-ink2 whitespace-pre-wrap">
                {m.content}
              </div>
              <MsgActions
                at={m.created_at}
                copied={copiedIdx === i}
                onCopy={() => copy(m.content, i)}
                align="end"
              />
            </div>
          ) : (
            // assistant turn: full-width, no bubble, like ChatGPT
            <div key={i} className="group flex flex-col items-start">
              <div className="w-full text-sm text-body leading-relaxed">
                <Markdown>{m.content}</Markdown>
              </div>
              <MsgActions
                at={m.created_at}
                copied={copiedIdx === i}
                onCopy={() => copy(m.content, i)}
                align="start"
              />
            </div>
          ),
        )}
        {thinking && (
          <div className="flex items-center gap-1.5 text-sub" aria-label="Agent is thinking">
            <span className="typing-dot" />
            <span className="typing-dot" style={{ animationDelay: '0.15s' }} />
            <span className="typing-dot" style={{ animationDelay: '0.3s' }} />
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="shrink-0 p-3 border-t border-tile bg-bg">
        <div className="flex items-center gap-2 rounded-xl border border-line/70 bg-surface pl-4 pr-1.5 py-1.5
                        focus-within:border-line transition-colors shadow-sm">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder={placeholder}
            className="flex-1 bg-transparent text-sm placeholder:text-sub/50 focus:outline-none py-1"
          />
          <button
            onClick={() => send()}
            disabled={thinking || !input.trim()}
            aria-label="Send message"
            className="h-8 w-8 shrink-0 rounded-full grid place-items-center transition-colors
                       bg-ink2 text-bg hover:bg-ink disabled:bg-tile disabled:text-sub/50"
          >
            <SendIcon />
          </button>
        </div>
      </div>
    </>
  )
}

function MsgActions({
  at,
  copied,
  onCopy,
  align,
}: {
  at?: string
  copied: boolean
  onCopy: () => void
  align: 'start' | 'end'
}) {
  return (
    <div
      className={`flex items-center gap-1 mt-1 opacity-0 group-hover:opacity-100 transition-opacity ${
        align === 'end' ? 'flex-row-reverse' : ''
      }`}
    >
      <button
        onClick={onCopy}
        title={copied ? 'Copied' : 'Copy'}
        aria-label={copied ? 'Copied' : 'Copy message'}
        className="h-6 w-6 grid place-items-center rounded-md text-sub/70 hover:text-body hover:bg-tile/70 transition-colors"
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </button>
      {at && <span className="text-[10px] text-sub/50 px-0.5">{fmtDate(at)}</span>}
    </div>
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
      className={`h-8 w-8 grid place-items-center rounded-lg transition-colors ${
        active ? 'bg-tile text-ink2' : 'text-sub/80 hover:text-ink2 hover:bg-tile/70'
      }`}
    >
      {children}
    </button>
  )
}

// Stroke icons in the OpenAI style (heroicons-outline paths, 1.7px stroke).
function Svg({ d, size = 18 }: { d: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={d} />
    </svg>
  )
}

const NewChatIcon = () => (
  <Svg d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125M18 14v4.75A2.25 2.25 0 0115.75 21H5.25A2.25 2.25 0 013 18.75V8.25A2.25 2.25 0 015.25 6H10" />
)
const ClockIcon = () => <Svg d="M12 6v6h4.5M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
const TrashIcon = () => (
  <Svg d="M14.74 9l-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 01-2.244 2.077H8.084a2.25 2.25 0 01-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 00-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 013.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 00-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 00-7.5 0" />
)
const CopyIcon = () => (
  <Svg
    size={15}
    d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 2h8a2 2 0 012 2v8a2 2 0 01-2 2h-8a2 2 0 01-2-2v-8a2 2 0 012-2z"
  />
)
const CheckIcon = () => <Svg size={15} d="M4.5 12.75l6 6 9-13.5" />
const BubbleIcon = () => (
  <Svg
    size={15}
    d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm3.75 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm3.75 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM21 12c0 4.556-4.03 8.25-9 8.25a9.76 9.76 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z"
  />
)
const SendIcon = () => <Svg size={16} d="M12 19V5m0 0l-6 6m6-6l6 6" />
