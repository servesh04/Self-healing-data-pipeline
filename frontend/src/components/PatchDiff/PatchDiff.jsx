const SECTION_LABEL = { renames: 'renames', casts: 'casts', null_policy: 'null_policy', drops: 'drops' }

function diffLines(patch) {
  if (!patch) return []
  const lines = []
  for (const [section, value] of Object.entries(patch)) {
    if (!value || (Array.isArray(value) && value.length === 0) || Object.keys(value).length === 0) {
      continue
    }
    if (Array.isArray(value)) {
      for (const item of value) lines.push(`${SECTION_LABEL[section] ?? section}: - ${item}`)
    } else {
      for (const [k, v] of Object.entries(value)) {
        lines.push(`${SECTION_LABEL[section] ?? section}: ${k} → ${v}`)
      }
    }
  }
  return lines
}

export default function PatchDiff({ detail }) {
  if (!detail) return null

  // Once a patch has been applied, detail.confidence reflects whatever the
  // MOST RECENT diagnose call produced — which, after a loop iteration or a
  // failed re-diagnosis, is no longer the confidence that actually produced
  // this patch (e.g. day4_combo: attempt 1 applies at confidence 0.96, but a
  // second diagnose call can fail and reset the top-level field to 0.0,
  // making an already-applied high-confidence patch look like a bad guess).
  // Prefer the history entry that recorded this exact application.
  const showingApplied = Boolean(detail.applied_patch)
  const lastHistoryEntry = detail.history?.[detail.history.length - 1]
  const patch = detail.applied_patch ?? detail.proposed_patch
  const lines = diffLines(patch)
  const confidence = showingApplied ? (lastHistoryEntry?.confidence ?? detail.confidence) : detail.confidence
  const isHigh = confidence != null && confidence >= 0.75

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-neutral-500">
          {showingApplied ? 'Applied patch' : 'Proposed patch'}
        </h3>
        {confidence != null && (
          <span
            className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${
              isHigh
                ? 'border-emerald-600 bg-emerald-950/60 text-emerald-300'
                : 'border-amber-600 bg-amber-950/60 text-amber-300'
            }`}
          >
            confidence {confidence.toFixed(2)}
          </span>
        )}
      </div>

      {(() => {
        // Same staleness issue as confidence above: prefer the class implied
        // by the history entry this patch actually came from.
        const driftClass = showingApplied
          ? (lastHistoryEntry?.specialist?.replace('spec_', '') ?? detail.drift_class)
          : detail.drift_class
        return (
          driftClass && (
            <p className="mb-2 font-mono text-[11px] text-neutral-500">
              drift_class: <span className="text-neutral-300">{driftClass}</span>
            </p>
          )
        )
      })()}

      {lines.length > 0 ? (
        <pre className="overflow-x-auto rounded border border-neutral-800 bg-black px-3 py-2 font-mono text-xs leading-relaxed">
          {lines.map((line, i) => (
            <div key={i} className="text-emerald-400">
              + {line}
            </div>
          ))}
        </pre>
      ) : (
        <p className="text-xs text-neutral-600">no patch proposed</p>
      )}

      {detail.patch_rationale && (
        <p className="mt-2 border-l-2 border-neutral-700 pl-2 text-xs italic text-neutral-400">
          {detail.patch_rationale}
        </p>
      )}
    </div>
  )
}
