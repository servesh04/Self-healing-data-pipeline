import { useState } from 'react'
import { useRunStore } from '../../store/useRunStore'
import { DATASETS } from '../../lib/datasets'

export default function NewRunModal() {
  const open = useRunStore((s) => s.newRunOpen)
  const close = useRunStore((s) => s.closeNewRun)
  const trigger = useRunStore((s) => s.trigger)
  const triggering = useRunStore((s) => s.triggering)
  const triggerError = useRunStore((s) => s.triggerError)
  const [dataset, setDataset] = useState(DATASETS[3].file)

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/70 px-4"
      onClick={close}
    >
      <div
        className="w-full max-w-sm rounded-lg border border-line-strong bg-surface p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Trigger a run
        </h2>
        <select
          value={dataset}
          onChange={(e) => setDataset(e.target.value)}
          className="w-full rounded border border-line-strong bg-surface-raised px-2 py-1.5 text-sm text-ink focus:border-ink-faint focus:outline-none"
        >
          {DATASETS.map((d) => (
            <option key={d.file} value={d.file}>
              {d.label}
            </option>
          ))}
        </select>
        {triggerError && <p className="mt-2 text-xs text-fail">{triggerError}</p>}
        <div className="mt-3 flex justify-end gap-2">
          <button
            onClick={close}
            className="rounded border border-line-strong px-3 py-1.5 text-xs text-ink-muted hover:text-ink"
          >
            Cancel
          </button>
          <button
            onClick={() => trigger(dataset)}
            disabled={triggering}
            className="rounded border border-signal bg-signal/15 px-3 py-1.5 text-xs font-medium text-signal hover:bg-signal/25 disabled:opacity-50"
          >
            {triggering ? 'starting…' : 'Run'}
          </button>
        </div>
      </div>
    </div>
  )
}
