import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const AXIS_TICK = { fill: 'var(--color-ink-muted)', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="rounded border border-line-strong bg-surface-raised px-2.5 py-1.5 font-mono text-[11px] text-ink shadow-lg">
      <div className="text-ink-muted">{label}</div>
      <div>{row.attempts === 0 ? 'no data' : `${row.passed}/${row.attempts} passed`}</div>
    </div>
  )
}

// A specialist with zero attempts renders as an explicit "no data" row
// rather than being hidden — an absent row looks like a bug, per spec.
export default function SpecialistPerformance({ data }) {
  // A truly zero-width bar gives Recharts nowhere sensible to anchor its
  // LabelList (position="right" silently fails to render at all) — a
  // negligible sliver (invisible at this chart width) keeps "no data"
  // visible for a zero-attempt specialist instead of it vanishing.
  const rows = data.map((d) => ({
    ...d,
    display_rate: d.success_rate ?? 0.004,
    count_label: d.attempts === 0 ? 'no data' : `${d.passed}/${d.attempts}`,
  }))

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        Specialist performance
      </h3>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 48, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--color-line)" strokeOpacity={0.6} horizontal={false} />
          {/* Visible % ticks, not just gridlines — a bar's length reads
              unambiguously against a labelled scale; eyeballing proportion
              against a same-color-as-background gridline is exactly how a
              0.556-width bar gets misread as ~90%. */}
          <XAxis
            type="number"
            domain={[0, 1]}
            ticks={[0, 0.25, 0.5, 0.75, 1]}
            tickFormatter={(v) => `${Math.round(v * 100)}%`}
            tick={AXIS_TICK}
            axisLine={{ stroke: 'var(--color-line)' }}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="specialist"
            tick={AXIS_TICK}
            axisLine={{ stroke: 'var(--color-line)' }}
            tickLine={false}
            width={110}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: 'var(--color-surface-raised)' }} />
          <Bar dataKey="display_rate" fill="var(--color-pass)" radius={[0, 2, 2, 0]} barSize={16}>
            <LabelList dataKey="count_label" position="right" fill="var(--color-ink-faint)" fontSize={11} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
