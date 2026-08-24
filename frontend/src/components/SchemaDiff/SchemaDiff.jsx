const STATUS_STYLE = {
  missing: 'border-red-700 bg-red-950/60 text-red-300',
  unexpected: 'border-amber-600 bg-amber-950/60 text-amber-300',
  mismatch: 'border-orange-600 bg-orange-950/60 text-orange-300',
  matching: 'border-emerald-700 bg-emerald-950/40 text-emerald-300',
}

const STATUS_LABEL = {
  missing: 'missing',
  unexpected: 'unexpected',
  mismatch: 'type mismatch',
  matching: 'matching',
}

export default function SchemaDiff({ detail }) {
  if (!detail) return null

  const actual = detail.actual_schema || {}
  const expected = detail.expected_schema || {}
  const driftByColumn = new Map((detail.drift_items || []).map((d) => [d.column, d]))

  const columns = [...new Set([...Object.keys(expected), ...Object.keys(actual)])]

  if (columns.length === 0) {
    return (
      <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
          Schema diff
        </h3>
        <p className="text-xs text-neutral-600">no schema observed yet</p>
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
    <div className="rounded-lg border border-neutral-800 bg-neutral-950 p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-neutral-500">
        Schema diff
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] font-mono text-xs">
          <thead>
            <tr className="text-neutral-500">
              <th className="pb-1 text-left font-normal">column</th>
              <th className="pb-1 text-left font-normal">expected</th>
              <th className="pb-1 text-left font-normal">actual</th>
              <th className="pb-1 text-left font-normal">status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.col} className="border-t border-neutral-900">
                <td className="py-1 pr-2 text-neutral-300">{r.col}</td>
                <td className="py-1 pr-2 text-neutral-500">{r.expectedType}</td>
                <td className="py-1 pr-2 text-neutral-500">{r.actualType}</td>
                <td className="py-1">
                  <span className={`rounded border px-1.5 py-0.5 ${STATUS_STYLE[r.status]}`}>
                    {STATUS_LABEL[r.status]}
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
