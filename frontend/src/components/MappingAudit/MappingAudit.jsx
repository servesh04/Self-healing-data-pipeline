// The mapping_state table is append-only (backend/services/store.py) — every
// patch a run ever applied is already sitting in Postgres, tagged with that
// run's id. This is a read of that history, not a separate tracking
// mechanism: a small piece of data lineage that falls out for free.

function summarize(mapping) {
  const parts = []
  for (const [section, value] of Object.entries(mapping || {})) {
    if (!value) continue
    if (Array.isArray(value) && value.length) parts.push(`${section}: [${value.join(', ')}]`)
    else if (!Array.isArray(value) && Object.keys(value).length) {
      const entries = Object.entries(value).map(([k, v]) => `${k}→${v}`)
      parts.push(`${section}: ${entries.join(', ')}`)
    }
  }
  return parts.length ? parts.join('  ·  ') : '(no-op)'
}

export default function MappingAudit({ detail }) {
  const audit = detail?.mapping_audit || []
  if (audit.length === 0) return null

  return (
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        Mapping audit trail — this run
      </h3>
      <p className="mb-2 text-[11px] text-neutral-600">
        Every patch this run persisted to the live mapping, in order. The mapping store is
        append-only, so nothing here was ever overwritten — just superseded by the next row.
      </p>
      <ol className="space-y-1.5">
        {audit.map((entry, i) => (
          <li key={entry.id} className="flex gap-2 font-mono text-xs">
            <span className="shrink-0 text-neutral-600">#{i + 1}</span>
            <div className="min-w-0">
              <div className="truncate text-neutral-300">{summarize(entry.mapping)}</div>
              <div className="text-[10px] text-neutral-600">
                {entry.updated_by} · {new Date(entry.created_at).toLocaleTimeString()}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
