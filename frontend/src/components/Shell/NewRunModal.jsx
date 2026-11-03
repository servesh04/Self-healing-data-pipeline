import { useEffect, useState } from 'react'
import { useRunStore } from '../../store/useRunStore'
import { DATASETS } from '../../lib/datasets'

export default function NewRunModal() {
  const open = useRunStore((s) => s.newRunOpen)
  const close = useRunStore((s) => s.closeNewRun)
  const trigger = useRunStore((s) => s.trigger)
  const triggering = useRunStore((s) => s.triggering)
  const triggerError = useRunStore((s) => s.triggerError)
  const [dataset, setDataset] = useState(DATASETS[3].file)
  const contractName = useRunStore((s) => s.contractName)
  const sources = useRunStore((s) => s.sources)
  const choices = sources.length ? sources.map((s) => ({ file: s.source_id,
    label: `${s.source_id}${s.kind === 'database_table' ? ' (database table)' : s.kind === 'local_json' ? ' (local JSON)' : ''}`,
    available: s.available !== false })) :
    (contractName === 'compact' ? [{ file: 'compact_clean.csv', label: 'compact — clean control' }] : DATASETS)

  useEffect(() => { setDataset(choices.find((s) => s.available !== false)?.file ?? '') }, [contractName, sources])

  useEffect(() => {
    if (!open) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape' && !triggering) close()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [open, triggering, close])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-run-title"
        className="w-full max-w-sm rounded-lg border border-line-strong bg-surface p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="new-run-title" className="mb-3 text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Trigger a run
        </h2>
        <p className="mb-2 text-[11px] text-ink-faint">Contract: <span className="text-signal">{contractName}</span></p>
        <label htmlFor="run-dataset" className="sr-only">Dataset</label>
        <select
          id="run-dataset"
          value={dataset}
          onChange={(e) => setDataset(e.target.value)}
          className="w-full rounded border border-line-strong bg-surface-raised px-2 py-1.5 text-sm text-ink focus:border-signal-dim focus:outline-none"
        >
          {choices.map((d) => (
            <option key={d.file} value={d.file} disabled={!d.available}>
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
            disabled={triggering || !dataset}
            className="rounded border border-signal bg-signal/15 px-3 py-1.5 text-xs font-medium text-signal hover:bg-signal/25 disabled:opacity-50"
          >
            {triggering ? 'starting…' : 'Run'}
          </button>
        </div>
      </div>
    </div>
  )
}
