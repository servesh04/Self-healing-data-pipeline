function shortVersion(version) {
  return version ? `${version.slice(0, 10)}…` : '—'
}

function SourceBadge({ source }) {
  const uploaded = source === 'upload'
  return (
    <span
      className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
        uploaded
          ? 'border-signal-dim bg-signal/10 text-signal'
          : 'border-line-strong text-ink-faint'
      }`}
    >
      {uploaded ? 'uploaded' : 'file'}
    </span>
  )
}

export default function ContractsList({
  contracts = [],
  selectedName,
  loading = false,
  onSelect,
}) {
  return (
    <section className="rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Registered contracts
        </h3>
        {loading && <span className="text-[11px] text-ink-faint">loading…</span>}
      </div>

      {contracts.length === 0 && !loading ? (
        <p className="text-xs text-ink-faint">no contracts yet</p>
      ) : (
        <ul className="space-y-1">
          {contracts.map((contract) => {
            const selected = contract.name === selectedName
            return (
              <li key={contract.name}>
                <button
                  onClick={() => onSelect?.(contract.name)}
                  aria-current={selected ? 'true' : undefined}
                  className={`w-full rounded border px-2 py-1.5 text-left transition-colors ${
                    selected
                      ? 'border-signal-dim bg-signal/10'
                      : 'border-transparent hover:border-line-strong hover:bg-surface-raised'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className={`truncate font-mono text-xs ${selected ? 'text-signal' : 'text-ink'}`}>
                      {contract.name}
                    </span>
                    <SourceBadge source={contract.source} />
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-2 text-[10px] text-ink-faint">
                    <span className="truncate" title={contract.version}>
                      {shortVersion(contract.version)}
                    </span>
                    <span>{contract.column_count} col{contract.column_count === 1 ? '' : 's'}</span>
                  </div>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
