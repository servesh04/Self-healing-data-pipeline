import { useState } from 'react'
import { useRunStore } from '../../store/useRunStore'

export default function ApprovalPanel({ detail }) {
  const [note, setNote] = useState('')
  const approve = useRunStore((s) => s.approve)
  const reject = useRunStore((s) => s.reject)
  const approving = useRunStore((s) => s.approving)

  if (!detail?.awaiting_approval) return null

  return (
    <div className="rounded-lg border border-amber-700 bg-amber-950/20 p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-amber-400">
        Awaiting human approval
      </h3>
      <p className="mb-3 text-xs text-neutral-400">
        Confidence is below the auto-apply threshold. Review the proposed patch above, then
        approve or reject.
      </p>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="optional note"
        rows={2}
        className="mb-3 w-full rounded border border-neutral-700 bg-neutral-900 px-2 py-1 text-xs text-neutral-200 placeholder:text-neutral-600 focus:border-neutral-500 focus:outline-none"
      />
      <div className="flex gap-2">
        <button
          onClick={() => approve(note || undefined)}
          disabled={approving}
          className="flex-1 rounded border border-emerald-600 bg-emerald-950/60 px-3 py-1.5 text-sm font-medium text-emerald-300 hover:bg-emerald-900/60 disabled:opacity-50"
        >
          {approving ? 'working…' : 'Approve'}
        </button>
        <button
          onClick={() => reject(note || undefined)}
          disabled={approving}
          className="flex-1 rounded border border-red-700 bg-red-950/60 px-3 py-1.5 text-sm font-medium text-red-300 hover:bg-red-900/60 disabled:opacity-50"
        >
          {approving ? 'working…' : 'Reject'}
        </button>
      </div>
    </div>
  )
}
