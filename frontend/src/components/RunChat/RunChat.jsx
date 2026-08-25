import { useEffect, useRef, useState } from 'react'
import { postRunChat } from '../../api/client'

const SUGGESTED = ['Why did this escalate?', 'What did the specialist propose?', 'Why was confidence low?']

// A blank input invites the one question that produces a bad answer —
// these chips are required, not decoration (see the feature spec).
const MAX_SENT_MESSAGES = 10 // mirrors backend/routers/chat.py's own cap

// run_ids and patch-fragment JSON read as mono inline inside otherwise-sans
// chat prose — a plain regex split, not a markdown renderer.
const INLINE_MONO_RE = /([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\{[^{}]*\})/gi
function renderWithMono(text) {
  return text.split(INLINE_MONO_RE).map((part, i) =>
    /^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\{[^{}]*\})$/i.test(part) ? (
      <span key={i} className="font-mono text-[0.92em] text-ink">
        {part}
      </span>
    ) : (
      <span key={i}>{part}</span>
    )
  )
}

export default function RunChat({ runId }) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'nearest' })
  }, [messages, loading])

  async function send(text) {
    const content = text.trim()
    if (!content || loading) return
    const next = [...messages, { role: 'user', content }]
    setMessages(next)
    setInput('')
    setLoading(true)
    setError(null)
    try {
      const { reply } = await postRunChat(runId, next.slice(-MAX_SENT_MESSAGES))
      setMessages((m) => [...m, { role: 'assistant', content: reply }])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-2 font-sans">
      {messages.length === 0 && (
        <div className="flex flex-wrap gap-1.5">
          {SUGGESTED.map((q) => (
            <button
              key={q}
              onClick={() => send(q)}
              className="rounded-full border border-line-strong px-2.5 py-1 text-[11px] text-ink-muted hover:border-signal-dim hover:text-ink"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {messages.length > 0 && (
        <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
          {messages.map((m, i) => (
            <div
              key={i}
              className={`rounded px-2.5 py-1.5 text-xs leading-relaxed ${
                m.role === 'user' ? 'bg-surface-raised text-ink' : 'border border-line bg-canvas text-ink-muted'
              }`}
            >
              {m.role === 'assistant' ? renderWithMono(m.content) : m.content}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      )}

      {loading && <p className="text-[11px] text-ink-faint">thinking…</p>}
      {error && <p className="text-[11px] text-fail">{error}</p>}

      <form
        onSubmit={(e) => {
          e.preventDefault()
          send(input)
        }}
        className="flex gap-1.5"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this run…"
          maxLength={500}
          className="flex-1 rounded border border-line-strong bg-surface-raised px-2 py-1.5 text-xs text-ink placeholder:text-ink-faint focus:border-ink-faint focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="rounded border border-line-strong px-3 py-1.5 text-xs text-ink-muted hover:text-ink disabled:opacity-40"
        >
          send
        </button>
      </form>
    </div>
  )
}
