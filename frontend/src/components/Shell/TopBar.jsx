import { useEffect, useState } from 'react'
import { getPing } from '../../api/client'
import { useRunStore } from '../../store/useRunStore'

export default function TopBar() {
  const [appEnv, setAppEnv] = useState(null)
  const openNewRun = useRunStore((s) => s.openNewRun)
  const contracts = useRunStore((s) => s.contracts)
  const contractName = useRunStore((s) => s.contractName)
  const fetchContracts = useRunStore((s) => s.fetchContracts)
  const fetchSources = useRunStore((s) => s.fetchSources)
  const setContractName = useRunStore((s) => s.setContractName)

  useEffect(() => {
    getPing()
      .then((r) => setAppEnv(r.env))
      .catch(() => {})
    fetchContracts()
    fetchSources()
  }, [fetchContracts, fetchSources])

  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-3 px-8">
        <span className="text-lg text-signal" aria-hidden="true">
          {'↺'}
        </span>
        <h1 className="font-display text-base font-semibold tracking-tight text-ink">
          Self-Healing Pipeline
        </h1>
        {appEnv && (
          <span className="rounded border border-line-strong px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-ink-faint">
            env: {appEnv}
          </span>
        )}
        <label className="ml-3 text-[11px] text-ink-muted">Contract{' '}
          <select aria-label="Contract" value={contractName} onChange={(e) => setContractName(e.target.value)}
            className="rounded border border-line-strong bg-surface-raised px-2 py-1 text-xs text-ink">
            {(contracts.length ? contracts : [{ name: 'default' }]).map((c) =>
              <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        <button
          onClick={openNewRun}
          className="ml-auto rounded border border-line-strong bg-surface-raised px-3 py-1.5 text-xs font-medium text-ink hover:border-signal-dim"
        >
          + New run
        </button>
      </div>
    </header>
  )
}
