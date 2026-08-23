import { useCallback, useEffect, useState } from 'react'
import { apiBase, getHealth, getPing, probeAuthRejection } from './api/client'

/** Phase 0 status page: proves the deployed frontend reaches the deployed
 *  backend, that auth is enforced, and that Neon is writable. */
export default function App() {
  const [state, setState] = useState({ loading: true })

  const load = useCallback(async () => {
    setState({ loading: true })
    const out = { loading: false, checkedAt: new Date().toISOString() }
    try {
      out.health = await getHealth()
    } catch (e) {
      out.healthError = e.message
    }
    try {
      out.ping = await getPing()
    } catch (e) {
      out.pingError = `${e.status ?? ''} ${e.message}`.trim()
    }
    out.authProbe = await probeAuthRejection()
    setState(out)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const { loading, health, healthError, ping, pingError, authProbe } = state
  const db = ping?.database

  const checks = [
    {
      label: 'Frontend → backend reachable',
      ok: Boolean(health?.ok),
      detail: healthError ?? `GET /health → ${JSON.stringify(health ?? null)}`,
    },
    {
      label: 'Bearer auth rejects unauthenticated request',
      ok: Boolean(authProbe?.rejected),
      detail: authProbe
        ? `unauthenticated GET /api/ping → HTTP ${authProbe.status} (expected 401)`
        : '—',
    },
    {
      label: 'Authenticated request succeeds',
      ok: Boolean(ping) && !pingError,
      detail: pingError ?? `GET /api/ping → "${ping?.message ?? '—'}"`,
    },
    {
      label: 'Backend writes to Neon Postgres',
      ok: Boolean(db?.connected),
      detail: db?.connected
        ? `wrote row #${db.inserted_id} · ${db.total_rows} total · server ${db.server_version}`
        : (db?.error ?? '—'),
    },
  ]

  const allGreen = checks.every((c) => c.ok)

  return (
    <main className="min-h-screen bg-neutral-950 px-6 py-10 font-mono text-neutral-200">
      <div className="mx-auto max-w-3xl">
        <header className="mb-8 border-b border-neutral-800 pb-5">
          <h1 className="text-xl font-semibold text-neutral-50">
            Self-Healing Data Pipeline
          </h1>
          <p className="mt-1 text-sm text-neutral-500">
            Phase 0 — deployment skeleton. No pipeline, no LangGraph yet.
          </p>
          <p className="mt-3 text-xs text-neutral-600">
            api base: <span className="text-neutral-400">{apiBase || '(unset)'}</span>
          </p>
        </header>

        <div
          className={`mb-6 rounded border px-4 py-3 text-sm ${
            loading
              ? 'border-neutral-700 bg-neutral-900 text-neutral-400'
              : allGreen
                ? 'border-emerald-800 bg-emerald-950/40 text-emerald-300'
                : 'border-red-900 bg-red-950/40 text-red-300'
          }`}
        >
          {loading
            ? 'checking…'
            : allGreen
              ? 'PHASE 0 GREEN — all four checks passed'
              : 'PHASE 0 NOT GREEN — see failing checks below'}
        </div>

        <ul className="space-y-2">
          {checks.map((c) => (
            <li
              key={c.label}
              className="rounded border border-neutral-800 bg-neutral-900/60 px-4 py-3"
            >
              <div className="flex items-start gap-3">
                <span
                  className={`mt-0.5 shrink-0 text-xs font-bold ${
                    loading
                      ? 'text-neutral-600'
                      : c.ok
                        ? 'text-emerald-400'
                        : 'text-red-400'
                  }`}
                >
                  {loading ? '[ ··· ]' : c.ok ? '[ PASS ]' : '[ FAIL ]'}
                </span>
                <div className="min-w-0">
                  <div className="text-sm text-neutral-200">{c.label}</div>
                  <div className="mt-1 break-words text-xs text-neutral-500">
                    {c.detail}
                  </div>
                </div>
              </div>
            </li>
          ))}
        </ul>

        {ping?.versions && (
          <section className="mt-8">
            <h2 className="mb-2 text-xs uppercase tracking-wider text-neutral-500">
              Recorded versions
            </h2>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 rounded border border-neutral-800 bg-neutral-900/60 px-4 py-3 text-xs">
              {Object.entries(ping.versions).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <dt className="text-neutral-500">{k}</dt>
                  <dd className="text-neutral-300">{v}</dd>
                </div>
              ))}
            </dl>
          </section>
        )}

        <button
          onClick={load}
          disabled={loading}
          className="mt-8 rounded border border-neutral-700 bg-neutral-900 px-4 py-2 text-sm text-neutral-300 hover:border-neutral-500 hover:text-neutral-100 disabled:opacity-50"
        >
          {loading ? 'checking…' : 're-run checks'}
        </button>
      </div>
    </main>
  )
}
