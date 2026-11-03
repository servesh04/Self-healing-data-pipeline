const TYPE_LABEL = {
  int64: 'integer',
  float64: 'decimal',
  str: 'text',
  object: 'text (object)',
  bool: 'boolean',
  'datetime64[ns]': 'datetime',
}

function yesNo(value) {
  return value ? 'yes' : 'no'
}

function allowedText(allowed) {
  if (!allowed || allowed.length === 0) return '—'
  return allowed.join(', ')
}

/**
 * The contract preview table: one row per declared column with every
 * constraint the existing contract grammar supports. Shared by the upload
 * preview and the registered-contract detail view.
 */
export default function ContractPreview({ columns = [], title = 'Column preview' }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
          {title}
        </h3>
        <span className="text-[11px] text-ink-faint">
          {columns.length} column{columns.length === 1 ? '' : 's'}
        </span>
      </div>

      {columns.length === 0 ? (
        <p className="text-xs text-ink-faint">no columns declared</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] font-mono text-xs">
            <thead>
              <tr className="text-[11px] uppercase text-ink-faint">
                <th className="py-1.5 pr-3 text-left font-normal">column</th>
                <th className="py-1.5 pr-3 text-left font-normal">type</th>
                <th className="py-1.5 pr-3 text-left font-normal">nullable</th>
                <th className="py-1.5 pr-3 text-left font-normal">unique</th>
                <th className="py-1.5 pr-3 text-left font-normal">allowed values</th>
                <th className="py-1.5 text-right font-normal">minimum</th>
              </tr>
            </thead>
            <tbody>
              {columns.map((column) => (
                <tr key={column.name} className="border-t border-line">
                  <td className="py-1.5 pr-3 text-ink">{column.name}</td>
                  <td className="py-1.5 pr-3 text-ink-muted" title={column.dtype}>
                    {TYPE_LABEL[column.dtype] ?? column.dtype}
                    <span className="ml-1 text-[10px] text-ink-faint">{column.dtype}</span>
                  </td>
                  <td className={`py-1.5 pr-3 ${column.nullable ? 'text-signal' : 'text-ink-muted'}`}>
                    {yesNo(column.nullable)}
                  </td>
                  <td className={`py-1.5 pr-3 ${column.unique ? 'text-signal' : 'text-ink-muted'}`}>
                    {yesNo(column.unique)}
                  </td>
                  <td className="max-w-[22rem] truncate py-1.5 pr-3 text-ink-muted" title={allowedText(column.allowed)}>
                    {allowedText(column.allowed)}
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-ink-muted">
                    {column.min == null ? '—' : column.min}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
