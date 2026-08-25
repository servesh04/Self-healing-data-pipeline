import { useState } from 'react'
import { useRunStore } from '../../store/useRunStore'

export default function ApprovalPanel({ detail }) {
  const [note, setNote] = useState('')
  const approve = useRunStore((s) => s.approve)
  const reject = useRunStore((s) => s.reject)
  const approving = useRunStore((s) => s.approving)

  if (!detail?.awaiting_approval) return null

  return (
    <div className="rounded-lg border border-signal bg-signal/10 p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-signal">
        Awaiting human approval
      </h3>
      <p className="mb-3 text-xs text-ink-muted">
        Confidence is below the auto-apply threshold. Review the proposed patch above, then
        approve or reject.
      </p>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="optional note"
        rows={2}
        className="mb-3 w-full rounded border border-line-strong bg-surface-raised px-2 py-1 text-xs text-ink placeholder:text-ink-faint focus:border-signal-dim focus:outline-none"
      />
      <div className="flex gap-2">
        <button
          onClick={() => approve(note || undefined)}
          disabled={approving}
          className="flex-1 rounded border border-pass bg-pass/15 px-3 py-1.5 text-sm font-medium text-pass hover:bg-pass/25 disabled:opacity-50"
        >
          {approving ? 'working…' : 'Approve'}
        </button>
        <button
          onClick={() => reject(note || undefined)}
          disabled={approving}
          className="flex-1 rounded border border-fail bg-fail/15 px-3 py-1.5 text-sm font-medium text-fail hover:bg-fail/25 disabled:opacity-50"
        >
          {approving ? 'working…' : 'Reject'}
        </button>
      </div>
    </div>
  )
}
