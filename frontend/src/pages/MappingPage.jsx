import { useEffect, useState } from 'react'
import { useRunStore } from '../store/useRunStore'
import MappingTimeline from '../components/MappingTimeline/MappingTimeline'

export default function MappingPage() {
  const mappingHistory = useRunStore((s) => s.mappingHistory)
  const mappingHistoryLoading = useRunStore((s) => s.mappingHistoryLoading)
  const mappingHistoryError = useRunStore((s) => s.mappingHistoryError)
  const fetchMappingHistory = useRunStore((s) => s.fetchMappingHistory)
  // Off by default -- scripts/seed_dashboard.py resets the mapping before
  // every single seeded run (necessary so each dataset independently
  // reproduces its own characteristic drift, not one pre-solved by an
  // earlier run's leftover patch — see the script's own comment), which
  // buries the agent's real patches under an entry of seeding housekeeping
  // for every one of real content.
  const [showSeedResets, setShowSeedResets] = useState(false)

  useEffect(() => {
    fetchMappingHistory()
  }, [fetchMappingHistory])

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Mapping history
        </h2>
        <div className="flex items-center gap-3">
          {mappingHistoryLoading && <span className="text-[11px] text-ink-faint">loading…</span>}
          <label className="flex items-center gap-1.5 text-[11px] text-ink-muted">
            <input
              type="checkbox"
              checked={showSeedResets}
              onChange={(e) => setShowSeedResets(e.target.checked)}
              className="accent-signal"
            />
            show seed resets
          </label>
        </div>
      </div>
      {mappingHistoryError ? (
        <p className="text-xs text-fail">{mappingHistoryError}</p>
      ) : (
        <MappingTimeline history={mappingHistory} showSeedResets={showSeedResets} />
      )}
    </div>
  )
}
