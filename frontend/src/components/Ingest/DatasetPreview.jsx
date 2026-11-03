/**
 * Dataset preview panel: column inventory (name + inferred type) and the
 * first N rows of a completed upload, styled with the existing dashboard
 * table language (see components/RunsTable/RunsTable.jsx).
 */

const TYPE_CHIP = {
  integer: 'border-signal-dim bg-signal/10 text-signal',
  float: 'border-signal-dim bg-signal/10 text-signal',
  boolean: 'border-signal-dim bg-signal/10 text-signal',
  datetime: 'border-signal-dim bg-signal/10 text-signal',
  string: 'border-line-strong text-ink-muted',
}

export default function DatasetPreview({ dataset, rows, rowsReturned, truncated, loading, error }) {
  if (loading) return <p className="text-xs text-ink-faint">loading preview…</p>
  if (error) return <p className="text-xs text-fail">{error}</p>
  if (!dataset) return null

  const columns = dataset.columns ?? []
  const value = (row, name) => {
    const cell = row?.[name]
    if (cell === undefined || cell === null) return ''
    return typeof cell === 'string' ? cell : String(cell)
  }

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
          {dataset.filename}
        </h3>
        <div className="flex flex-wrap gap-3 text-[11px] text-ink-faint">
          <span>{(dataset.byte_size / 1024).toFixed(1)} KB</span>
          <span>{dataset.row_count.toLocaleString()} data rows</span>
          <span className="min-w-0 max-w-[220px] truncate" title={dataset.sha256}>
            sha256 {dataset.sha256.slice(0, 12)}…
          </span>
        </div>
      </div>

      <div className="mb-3 flex flex-wrap gap-1.5">
        {columns.map((col) => (
          <span
            key={col.name}
            className={`rounded-full border px-2.5 py-0.5 text-[11px] ${TYPE_CHIP[col.type] ?? TYPE_CHIP.string}`}
            title={`inferred type: ${col.type}`}
          >
            {col.name} · {col.type}
          </span>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="text-xs text-ink-faint">no rows to preview</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] font-mono text-xs">
            <thead>
              <tr className="text-[11px] uppercase text-ink-faint">
                {columns.map((col) => (
                  <th key={col.name} className="py-1.5 pr-3 text-left font-normal">
                    {col.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="text-ink-muted">
              {rows.map((row, index) => (
                <tr key={index} className="border-t border-line">
                  {columns.map((col) => (
                    <td key={col.name} className="max-w-[240px] truncate py-1.5 pr-3">
                      {value(row, col.name)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {truncated && (
        <p className="mt-2 text-[11px] text-ink-faint">
          showing the first {rowsReturned} of {dataset.row_count.toLocaleString()} rows
        </p>
      )}
    </div>
  )
}
