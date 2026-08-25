import { useRunStore } from '../../store/useRunStore'
import { runStatusChip } from '../../lib/statusStyles'
import { formatDuration, formatConfidence, formatTime } from '../../lib/format'

const FILTER_CHIPS = [
  [null, 'All'],
  ['healed', 'Healed'],
  ['escalated', 'Escalated'],
  ['awaiting_approval', 'Awaiting'],
  ['clean', 'Clean'],
]

// The single strongest "this is a product" signal in the app, per
// DASHBOARD.md — prioritised over every chart. Reused as-is for the
// Overview page's "recent runs" panel (no filter chips, small limit) and
// the standalone Runs page (filter chips, full list).
export default function RunsTable({ runs, loading, showFilters = false, filter, onFilterChange }) {
  const selectRun = useRunStore((s) => s.selectRun)

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
          {showFilters ? 'Runs' : 'Recent runs'}
        </h3>
        {loading && <span className="text-[11px] text-ink-faint">refreshing…</span>}
      </div>

      {showFilters && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {FILTER_CHIPS.map(([value, label]) => (
            <button
              key={label}
              onClick={() => onFilterChange?.(value)}
              className={`rounded-full border px-2.5 py-0.5 text-[11px] ${
                filter === value
                  ? 'border-signal-dim bg-signal/15 text-signal'
                  : 'border-line-strong text-ink-muted hover:text-ink'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {runs.length === 0 ? (
        <p className="text-xs text-ink-faint">no runs yet</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] font-mono text-xs">
            <thead>
              <tr className="text-[11px] uppercase text-ink-faint">
                <th className="py-1.5 pr-3 text-left font-normal">status</th>
                <th className="py-1.5 pr-3 text-left font-normal">dataset</th>
                <th className="py-1.5 pr-3 text-left font-normal">drift</th>
                <th className="py-1.5 pr-3 text-right font-normal">attempts</th>
                <th className="py-1.5 pr-3 text-right font-normal">confidence</th>
                <th className="py-1.5 pr-3 text-left font-normal">approved by</th>
                <th className="py-1.5 pr-3 text-right font-normal">duration</th>
                <th className="py-1.5 text-right font-normal">time</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const chip = runStatusChip(r.status ?? r.api_status, r.approved_by)
                return (
                  <tr
                    key={r.run_id}
                    onClick={() => selectRun(r.run_id)}
                    className="h-9 cursor-pointer border-b border-line hover:bg-surface-raised"
                  >
                    <td className="pr-3">
                      <span className={`rounded-full border px-2 py-0.5 text-[10px] ${chip.className}`}>
                        {chip.label}
                      </span>
                    </td>
                    <td className="pr-3 text-ink">{r.dataset ?? r.source_path?.split(/[\\/]/).pop()}</td>
                    <td className="pr-3 text-ink-muted">{r.drift_class ?? '—'}</td>
                    <td
                      className={`pr-3 text-right tabular-nums ${
                        r.heal_attempts >= 2 ? 'text-signal' : 'text-ink-muted'
                      }`}
                    >
                      {r.heal_attempts}
                    </td>
                    <td className="pr-3 text-right tabular-nums text-ink-muted">
                      {formatConfidence(r.final_confidence)}
                    </td>
                    <td className="pr-3 text-ink-faint">{r.approved_by ?? '—'}</td>
                    <td className="pr-3 text-right tabular-nums text-ink-faint">
                      {formatDuration(r.duration_ms)}
                    </td>
                    <td className="text-right tabular-nums text-ink-faint">
                      {formatTime(r.created_at)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
