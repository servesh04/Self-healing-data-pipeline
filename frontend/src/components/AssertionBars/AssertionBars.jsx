// The contract's declared columns (config/contract.yaml) — fixed and known,
// same reasoning as GraphCanvas's hand-rolled topology. Hardcoding this
// gives a STABLE set of bars across polls, so a bar can visibly flip red to
// green after a heal rather than failing bars simply disappearing and
// reappearing under different labels.
const CONTRACT_COLUMNS = ['order_id', 'customer_id', 'region', 'order_date', 'revenue']

export default function AssertionBars({ detail }) {
  if (!detail) return null

  const failures = detail.assertion_failure_details || []
  const failuresByColumn = new Map()
  for (const f of failures) {
    const list = failuresByColumn.get(f.column) || []
    list.push(f)
    failuresByColumn.set(f.column, list)
  }

  // Columns the contract declares, plus any extra (unexpected) column names
  // that showed up in a failure but aren't part of the contract — a rename
  // fault's "cust_id" needs a bar too, or it would silently vanish from view.
  const extraColumns = [...failuresByColumn.keys()].filter((c) => !CONTRACT_COLUMNS.includes(c))
  const rows = [...CONTRACT_COLUMNS, ...extraColumns]

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        Assertions
      </h3>
      <div className="space-y-1.5">
        {rows.map((col) => {
          const fails = failuresByColumn.get(col)
          const passed = !fails
          return (
            <div key={col} className="flex items-center gap-2 font-mono text-xs">
              <span className="w-28 shrink-0 truncate text-ink-muted">{col}</span>
              <div
                className={`h-5 flex-1 rounded transition-colors duration-500 ${
                  passed ? 'bg-pass/20 border border-pass' : 'bg-fail/15 border border-fail'
                }`}
              >
                <div
                  className={`flex h-full items-center px-2 text-[11px] ${
                    passed ? 'text-pass' : 'text-fail'
                  }`}
                >
                  {passed
                    ? 'pass'
                    : `fail — ${fails.map((f) => `${f.check} (${f.failure_count})`).join(', ')}`}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
