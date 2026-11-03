import { useEffect, useState } from 'react'
import ContractPreview from './ContractPreview'

function formatDateTime(iso) {
  if (!iso) return '—'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function shortVersion(version) {
  return version ? `${version.slice(0, 12)}…` : '—'
}

export default function ContractDetail({ detail, onDelete, deleting = false, error = null }) {
  const [confirming, setConfirming] = useState(false)

  useEffect(() => {
    setConfirming(false)
  }, [detail?.name])

  if (!detail) return null

  return (
    <div className="space-y-3">
      <section className="rounded-lg border border-line bg-surface p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-display text-lg font-semibold tracking-tight text-ink">
                {detail.name}
              </h2>
              <span
                className={`rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                  detail.deletable
                    ? 'border-signal-dim bg-signal/10 text-signal'
                    : 'border-line-strong text-ink-faint'
                }`}
              >
                {detail.deletable ? 'uploaded' : 'file'}
              </span>
            </div>
            <p className="mt-0.5 text-[11px] text-ink-faint">
              {detail.deletable
                ? 'Registered from an upload and stored in Postgres.'
                : 'Bundled contract from config/contracts — immutable.'}
            </p>
          </div>

          {detail.deletable && (
            <div className="flex items-center gap-2">
              {confirming ? (
                <>
                  <span className="text-[11px] text-ink-muted">Delete permanently?</span>
                  <button
                    onClick={() => setConfirming(false)}
                    disabled={deleting}
                    className="rounded border border-line-strong px-2.5 py-1 text-[11px] text-ink-muted hover:text-ink disabled:opacity-50"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => onDelete?.(detail.name)}
                    disabled={deleting}
                    className="rounded border border-fail bg-fail/15 px-2.5 py-1 text-[11px] font-medium text-fail hover:bg-fail/25 disabled:opacity-50"
                  >
                    {deleting ? 'deleting…' : 'Delete'}
                  </button>
                </>
              ) : (
                <button
                  onClick={() => setConfirming(true)}
                  className="rounded border border-line-strong px-2.5 py-1 text-[11px] text-ink-muted hover:border-fail hover:text-fail"
                >
                  Delete contract
                </button>
              )}
            </div>
          )}
        </div>

        {error && (
          <p role="alert" className="mt-3 rounded border border-fail/40 bg-fail/10 px-2 py-1.5 text-xs text-fail">
            {error}
          </p>
        )}

        <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-ink-faint">version (sha256)</dt>
            <dd className="break-all font-mono text-[11px] text-ink-muted" title={detail.version}>
              {detail.version}
            </dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-ink-faint">columns</dt>
            <dd className="font-mono text-ink">{detail.column_count}</dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-ink-faint">row_count_min</dt>
            <dd className="font-mono text-ink">{detail.row_count_min}</dd>
          </div>
          <div>
            <dt className="text-[10px] uppercase tracking-wide text-ink-faint">
              {detail.deletable ? 'registered' : 'source'}
            </dt>
            <dd className="font-mono text-ink-muted">
              {detail.deletable ? formatDateTime(detail.created_at) : 'config/contracts'}
            </dd>
          </div>
        </dl>
      </section>

      <ContractPreview columns={detail.column_preview} />

      <section className="rounded-lg border border-line bg-surface p-3">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Versions
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[440px] font-mono text-xs">
            <thead>
              <tr className="text-[11px] uppercase text-ink-faint">
                <th className="py-1.5 pr-3 text-left font-normal">version</th>
                <th className="py-1.5 pr-3 text-left font-normal">columns</th>
                <th className="py-1.5 pr-3 text-left font-normal">created</th>
                <th className="py-1.5 text-right font-normal">state</th>
              </tr>
            </thead>
            <tbody>
              {(detail.versions ?? []).map((version) => (
                <tr key={version.version} className="border-t border-line">
                  <td className="py-1.5 pr-3 text-ink-muted" title={version.version}>
                    {shortVersion(version.version)}
                  </td>
                  <td className="py-1.5 pr-3 text-ink-muted">{version.column_count}</td>
                  <td className="py-1.5 pr-3 text-ink-faint">{formatDateTime(version.created_at)}</td>
                  <td className="py-1.5 text-right">
                    <span
                      className={`rounded border px-1.5 py-0.5 text-[10px] ${
                        version.current
                          ? 'border-pass/40 bg-pass/10 text-pass'
                          : 'border-line-strong text-ink-faint'
                      }`}
                    >
                      {version.current ? 'current' : 'historical'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-[11px] text-ink-faint">
          Every run stores the exact spec hash it used, so historical runs keep replaying their pinned snapshot even if a contract is later removed.
        </p>
      </section>

      <details className="rounded-lg border border-line bg-surface">
        <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Raw specification
        </summary>
        <pre className="overflow-x-auto border-t border-line p-3 font-mono text-[11px] text-ink-muted">
          {JSON.stringify(detail.spec, null, 2)}
        </pre>
      </details>
    </div>
  )
}
