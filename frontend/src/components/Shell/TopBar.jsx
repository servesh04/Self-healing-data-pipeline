import { useEffect, useState } from 'react'
import { getPing } from '../../api/client'
import { useRunStore } from '../../store/useRunStore'

export default function TopBar() {
  const [appEnv, setAppEnv] = useState(null)
  const openNewRun = useRunStore((s) => s.openNewRun)

  useEffect(() => {
    getPing()
      .then((r) => setAppEnv(r.env))
      .catch(() => {})
  }, [])

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
