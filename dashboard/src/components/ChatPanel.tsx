import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'

interface Msg {
  role: string
  content: string
}

// Agent replies may contain [Name](lead:12) — render those as profile links.
// (Syntax documented in docs/BRIEFING-UI.md; Toby's prompts emit it.)
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

export function ChatPanel() {
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.chatHistory().then(setMsgs).catch(() => {})
  }, [])
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [msgs, thinking])

  const send = async (preset?: string) => {
    const message = (preset ?? input).trim()
    if (!message || thinking) return
    setInput('')
    setMsgs((m) => [...m, { role: 'user', content: message }])
    setThinking(true)
    try {
      const { reply } = await api.chat(message)
      setMsgs((m) => [...m, { role: 'agent', content: reply }])
    } catch {
      setMsgs((m) => [...m, { role: 'agent', content: '⚠ Could not reach the agent.' }])
    } finally {
      setThinking(false)
    }
  }

  return (
    <>
      <div className="px-4 py-3 border-b border-zinc-800 text-sm font-semibold">
        Chat with your agent
        <div className="text-xs font-normal text-zinc-500">
          Same agent as Discord — ask about leads, bookings, follow-ups
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
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
          <div
            key={i}
            className={`max-w-[85%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap ${
              m.role === 'user'
                ? 'ml-auto bg-emerald-600/20 text-emerald-100'
                : 'bg-zinc-800/80 text-zinc-200'
            }`}
          >
            {renderWithLinks(m.content)}
          </div>
        ))}
        {thinking && (
          <div className="bg-zinc-800/80 rounded-xl px-3 py-2 text-sm text-zinc-400 w-fit">
            <span className="animate-pulse">Agent is thinking…</span>
          </div>
        )}
        <div ref={bottom} />
      </div>
      <div className="p-3 border-t border-zinc-800 flex gap-2">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && send()}
          placeholder="Which Bellevue buyers need a follow-up?"
          className="flex-1 rounded-lg bg-zinc-900 border border-zinc-800 px-3 py-2 text-sm
                     placeholder:text-zinc-600 focus:outline-none focus:border-emerald-500"
        />
        <button
          onClick={() => send()}
          disabled={thinking}
          className="rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 px-3 py-2 text-sm"
        >
          Send
        </button>
      </div>
    </>
  )
}
