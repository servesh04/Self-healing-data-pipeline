import { useRunStore } from '../../store/useRunStore'
import { diffMappings } from '../../lib/mappingDiff'

// Build last, cut first (DASHBOARD.md) — lands well with anyone who thinks
// about data lineage, but least load-bearing. Vertical timeline, one entry
// per mapping version: timestamp, run link, and the patch as a +/- diff.
//
// Diffs are computed over the FULL, unfiltered history first (each entry
// against its true immediate predecessor), and only THEN is the seed-reset
// noise filtered out of what's rendered — filtering before diffing would
// diff real patches against the wrong baseline and produce nonsense.
export default function MappingTimeline({ history, showSeedResets }) {
  const selectRun = useRunStore((s) => s.selectRun)

  const rows = history.map((entry, i) => ({
    entry,
    lines: diffMappings(i > 0 ? history[i - 1].mapping : null, entry.mapping),
    isSeedReset: entry.updated_by === 'seed-reset',
  }))
  const visible = showSeedResets ? rows : rows.filter((r) => !r.isSeedReset)

  if (history.length === 0) {
    return <p className="text-xs text-ink-faint">no mapping changes yet</p>
  }
  if (visible.length === 0) {
    return <p className="text-xs text-ink-faint">nothing but seed-reset entries — toggle "show seed resets" above</p>
  }

  return (
    <ol className="space-y-3">
      {visible.map(({ entry, lines }) => (
        <li key={entry.id} className="rounded-lg border border-line bg-surface p-3">
          <div className="mb-1.5 flex items-center justify-between text-[11px] text-ink-faint">
            <span>{entry.updated_by}</span>
            <span>{new Date(entry.created_at).toLocaleString()}</span>
          </div>
          {entry.run_id && (
            <button
              onClick={() => selectRun(entry.run_id)}
              className="mb-1.5 block font-mono text-[11px] text-signal underline decoration-dotted decoration-signal-dim hover:text-ink"
            >
              run {entry.run_id.slice(0, 8)}…
            </button>
          )}
          {lines.length === 0 ? (
            <p className="text-xs text-ink-faint">(no-op)</p>
          ) : (
            <div className="space-y-0.5 font-mono text-xs">
              {lines.map((l, j) => (
                <div key={j} className={l.sign === '+' ? 'text-pass' : 'text-fail'}>
                  {l.sign} {l.text}
                </div>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  )
}
