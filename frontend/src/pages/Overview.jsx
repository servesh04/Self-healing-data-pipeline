import { useEffect } from 'react'
import { useRunStore } from '../store/useRunStore'
import KpiStrip from '../components/KpiStrip/KpiStrip'
import RunsTable from '../components/RunsTable/RunsTable'
import ConfidenceChart from '../components/ConfidenceChart/ConfidenceChart'
import DriftDistribution from '../components/DriftDistribution/DriftDistribution'
import SpecialistPerformance from '../components/SpecialistPerformance/SpecialistPerformance'
import GlobalChat from '../components/GlobalChat/GlobalChat'

// "The system's behaviour at rest" (DASHBOARD.md) — an aggregate snapshot,
// not a live single-run view, so this fetches once per visit rather than
// polling every 2s like RunDetail does; re-opening the tab refreshes it.
export default function Overview() {
  const overviewRuns = useRunStore((s) => s.overviewRuns)
  const overviewRunsLoading = useRunStore((s) => s.overviewRunsLoading)
  const fetchOverviewRuns = useRunStore((s) => s.fetchOverviewRuns)
  const analytics = useRunStore((s) => s.analytics)
  const fetchAnalytics = useRunStore((s) => s.fetchAnalytics)

  useEffect(() => {
    fetchOverviewRuns()
    fetchAnalytics()
  }, [fetchOverviewRuns, fetchAnalytics])

  const unknownEscalated = analytics.driftDistribution.find((d) => d.drift_class === 'unknown')?.escalated

  if (analytics.error) {
    return <p className="text-xs text-fail">{analytics.error}</p>
  }

  // Each analytics call walks every run's checkpoint through the
  // checkpointer (services/analytics.py) — real Postgres round-trips, not
  // instant even parallelized. Loading is worth surfacing explicitly here;
  // an empty KPI strip and empty charts look identical to "no runs exist
  // yet" otherwise, which reads as broken rather than "still loading."
  if (analytics.loading && !analytics.summary) {
    return <p className="text-xs text-ink-faint">loading analytics…</p>
  }

  return (
    <div className="space-y-4">
      <KpiStrip summary={analytics.summary} unknownEscalated={unknownEscalated} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
        <RunsTable runs={overviewRuns} loading={overviewRunsLoading} />
        <div className="space-y-4">
          <ConfidenceChart data={analytics.confidence} />
          <DriftDistribution data={analytics.driftDistribution} />
        </div>
      </div>

      <SpecialistPerformance data={analytics.specialistPerformance} />

      {/* Read-only chat over all-run data — new feature, no relation to the
          healing graph. Placed below Specialist Performance per the feature
          spec's frontend requirements. Open by default, same as per-run's
          panel on RunDetail. */}
      <details open className="rounded-lg border border-line bg-surface">
        <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
          Ask about the pipeline
        </summary>
        <div className="border-t border-line p-3 pt-2">
          <GlobalChat />
        </div>
      </details>
    </div>
  )
}
