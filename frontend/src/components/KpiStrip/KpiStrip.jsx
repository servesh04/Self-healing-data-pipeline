// Four cards, no sparklines, no trend arrows — DASHBOARD.md: "Twenty runs
// over one day is not a time series and faking one is worse than omitting
// it." Large figure in the display serif, label above in tracked uppercase
// mono, sub-line below in the faintest ink tone.

function Kpi({ label, figure, sub }) {
  return (
    <div className="flex h-28 flex-col justify-center rounded-lg border border-line bg-surface px-4">
      <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted">
        {label}
      </div>
      <div className="mt-1 font-display text-3xl font-semibold tabular-nums text-ink">{figure}</div>
      <div className="mt-0.5 text-xs text-ink-faint">{sub}</div>
    </div>
  )
}

export default function KpiStrip({ summary, unknownEscalated }) {
  if (!summary) return null
  const autoHealPct = Math.round((summary.auto_heal_rate ?? 0) * 100)

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Kpi label="Runs" figure={summary.total_runs} sub={`${summary.drifted_runs} drift`} />
      <Kpi
        label="Auto-heal"
        figure={`${autoHealPct}%`}
        sub={`${summary.auto_healed} of ${summary.drifted_runs}`}
      />
      <Kpi
        label="Mean"
        figure={summary.mean_heal_attempts}
        sub={`attempts, of ${summary.attempted_runs} attempted`}
      />
      <Kpi
        label="Escalated"
        figure={summary.escalated}
        sub={unknownEscalated != null ? `${unknownEscalated} unknown` : ' '}
      />
    </div>
  )
}
