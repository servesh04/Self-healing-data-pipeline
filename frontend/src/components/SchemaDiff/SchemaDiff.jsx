import { SCHEMA_STATUS_STYLE, SCHEMA_STATUS_LABEL } from '../../lib/statusStyles'

export default function SchemaDiff({ detail }) {
  if (!detail) return null

  const actual = detail.actual_schema || {}
  const expected = detail.expected_schema || {}
  const driftByColumn = new Map((detail.drift_items || []).map((d) => [d.column, d]))

  const columns = [...new Set([...Object.keys(expected), ...Object.keys(actual)])]

  if (columns.length === 0) {
    return (
      <div className="rounded-lg border border-line bg-surface p-3">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Schema diff
        </h3>
        <p className="text-xs text-ink-faint">no schema observed yet</p>
      </div>
    )
  }

  const rows = columns.map((col) => {
    const inExpected = col in expected
    const inActual = col in actual
    const drift = driftByColumn.get(col)
    let status = 'matching'
    if (!inActual && inExpected) status = 'missing'
    else if (inActual && !inExpected) status = 'unexpected'
    else if (drift && drift.kind === 'dtype_mismatch') status = 'mismatch'
    return { col, expectedType: expected[col] ?? '—', actualType: actual[col] ?? '—', status }
  })

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        Schema diff
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] font-mono text-xs">
          <thead>
            <tr className="text-ink-muted">
              <th className="pb-1 text-left font-normal">column</th>
              <th className="pb-1 text-left font-normal">expected</th>
              <th className="pb-1 text-left font-normal">actual</th>
              <th className="pb-1 text-left font-normal">status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.col} className="border-t border-line">
                <td className="py-1 pr-2 text-ink">{r.col}</td>
                <td className="py-1 pr-2 text-ink-muted">{r.expectedType}</td>
                <td className="py-1 pr-2 text-ink-muted">{r.actualType}</td>
                <td className="py-1">
                  <span className={`rounded border px-1.5 py-0.5 ${SCHEMA_STATUS_STYLE[r.status]}`}>
                    {SCHEMA_STATUS_LABEL[r.status]}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
