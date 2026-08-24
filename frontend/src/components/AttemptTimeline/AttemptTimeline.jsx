const RESULT_STYLE = {
  passed: 'border-emerald-600 text-emerald-300',
  failed: 'border-red-700 text-red-300',
  rejected: 'border-neutral-600 text-neutral-400',
}

export default function AttemptTimeline({ detail }) {
  const history = detail?.history || []
  if (history.length === 0) return null

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        Attempt timeline
      </h3>
      <ol className="space-y-2">
        {history.map((h) => (
          <li key={h.attempt_no} className="flex gap-3 font-mono text-xs">
            <span className="mt-0.5 shrink-0 rounded border border-neutral-700 bg-neutral-900 px-1.5 py-0.5 text-neutral-400">
              #{h.attempt_no}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-neutral-300">{h.specialist}</span>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] uppercase ${RESULT_STYLE[h.result] ?? 'border-neutral-700 text-neutral-400'}`}>
                  {h.result}
                </span>
                <span className="text-neutral-600">confidence {h.confidence?.toFixed?.(2) ?? h.confidence}</span>
                {h.approved_by && <span className="text-neutral-600">via {h.approved_by}</span>}
              </div>
              <div className="mt-0.5 truncate text-neutral-500">{h.drift_summary}</div>
              {h.result === 'failed' && h.assertion_failures?.length > 0 && (
                <div className="mt-0.5 truncate text-red-400/80">
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
