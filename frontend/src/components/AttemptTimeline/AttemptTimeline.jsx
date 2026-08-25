import { ATTEMPT_RESULT_STYLE, ATTEMPT_RESULT_FALLBACK } from '../../lib/statusStyles'

export default function AttemptTimeline({ detail }) {
  const history = detail?.history || []
  if (history.length === 0) return null

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        Attempt timeline
      </h3>
      <ol className="space-y-2">
        {history.map((h) => (
          <li key={h.attempt_no} className="flex gap-3 font-mono text-xs">
            <span className="mt-0.5 shrink-0 rounded border border-line-strong bg-surface-raised px-1.5 py-0.5 text-ink-muted">
              #{h.attempt_no}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-ink">{h.specialist}</span>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${ATTEMPT_RESULT_STYLE[h.result] ?? ATTEMPT_RESULT_FALLBACK}`}>
                  {h.result}
                </span>
                <span className="text-ink-faint">confidence {h.confidence?.toFixed?.(2) ?? h.confidence}</span>
                {h.approved_by && <span className="text-ink-faint">via {h.approved_by}</span>}
              </div>
              <div className="mt-0.5 truncate text-ink-muted">{h.drift_summary}</div>
              {h.result === 'failed' && h.assertion_failures?.length > 0 && (
                <div className="mt-0.5 truncate text-fail/80">
                  still failing: {h.assertion_failures[0]}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
