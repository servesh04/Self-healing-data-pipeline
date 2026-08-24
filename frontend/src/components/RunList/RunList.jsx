import { useEffect, useState } from 'react'
import { useRunStore } from '../../store/useRunStore'

// The 6 hand-authored fault datasets (data/sources/) — trigger a run against
// any of them directly rather than requiring an upload flow, which is a
// Phase 5+ concern this dashboard doesn't need for the demo.
const DATASETS = [
  { file: 'day1_clean.csv', label: 'day1 — clean (control)' },
  { file: 'day2_renamed.csv', label: 'day2 — renamed column' },
  { file: 'day3_type_drift.csv', label: 'day3 — type drift' },
  { file: 'day4_combo.csv', label: 'day4 — combo (the cycle)' },
  { file: 'day5_unfixable.csv', label: 'day5 — unfixable (escalates)' },
  { file: 'day6_ambiguous_rename.csv', label: 'day6 — ambiguous (human review)' },
]

const STATUS_BADGE = {
  clean: 'border-emerald-600 bg-emerald-950/60 text-emerald-300',
  healed: 'border-emerald-600 bg-emerald-950/60 text-emerald-300',
  escalated: 'border-red-700 bg-red-950/60 text-red-300',
  awaiting_approval: 'border-amber-600 bg-amber-950/60 text-amber-300',
  running: 'border-sky-600 bg-sky-950/60 text-sky-300',
}

export default function RunList() {
  const runs = useRunStore((s) => s.runs)
  const runsLoading = useRunStore((s) => s.runsLoading)
  const fetchRuns = useRunStore((s) => s.fetchRuns)
  const selectRun = useRunStore((s) => s.selectRun)
  const trigger = useRunStore((s) => s.trigger)
  const triggering = useRunStore((s) => s.triggering)
  const triggerError = useRunStore((s) => s.triggerError)
  const [dataset, setDataset] = useState(DATASETS[3].file)

  useEffect(() => {
    fetchRuns()
    const id = setInterval(fetchRuns, 3000)
    return () => clearInterval(id)
  }, [fetchRuns])

  const handleTrigger = async () => {
    // The backend resolves a bare filename against its own data/sources/
    // directory — the browser has no useful notion of the server's
    // filesystem, so there is nothing more specific to send from here.
    await trigger(dataset)
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Trigger a run
        </h2>
        <div className="flex flex-wrap gap-2">
          <select
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="rounded border border-neutral-700 bg-neutral-900 px-2 py-1.5 text-sm text-neutral-200 focus:border-neutral-500 focus:outline-none"
          >
            {DATASETS.map((d) => (
              <option key={d.file} value={d.file}>
                {d.label}
              </option>
            ))}
          </select>
          <button
            onClick={handleTrigger}
            disabled={triggering}
            className="rounded border border-neutral-600 bg-neutral-800 px-3 py-1.5 text-sm text-neutral-200 hover:border-neutral-400 disabled:opacity-50"
          >
            {triggering ? 'starting…' : 'Run'}
          </button>
        </div>
        {triggerError && <p className="mt-2 text-xs text-red-400">{triggerError}</p>}
      </div>

      <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
            Runs
          </h2>
          {runsLoading && <span className="text-[11px] text-neutral-600">refreshing…</span>}
        </div>
        {runs.length === 0 ? (
          <p className="text-xs text-neutral-600">no runs yet</p>
        ) : (
          <ul className="space-y-1.5">
            {runs.map((r) => (
              <li key={r.run_id}>
                <button
                  onClick={() => selectRun(r.run_id)}
                  className="flex w-full items-center gap-3 rounded border border-neutral-800 bg-neutral-900/60 px-3 py-2 text-left hover:border-neutral-600"
                >
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 font-mono text-[10px] uppercase ${
                      STATUS_BADGE[r.api_status] ?? 'border-neutral-700 text-neutral-400'
                    }`}
                  >
                    {r.api_status}
                  </span>
                  <span className="min-w-0 flex-1 truncate font-mono text-xs text-neutral-300">
                    {r.source_path.split(/[\\/]/).pop()}
                  </span>
                  {r.heal_attempts > 0 && (
                    <span className="shrink-0 font-mono text-[11px] text-amber-400">
                      {'↺'} {r.heal_attempts}
                    </span>
                  )}
                  <span className="shrink-0 font-mono text-[10px] text-neutral-600">
                    {new Date(r.created_at).toLocaleTimeString()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
