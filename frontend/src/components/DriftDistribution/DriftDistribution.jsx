import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const AXIS_TICK = { fill: 'var(--color-ink-muted)', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="rounded border border-line-strong bg-surface-raised px-2.5 py-1.5 font-mono text-[11px] text-ink shadow-lg">
      <div className="text-ink-muted">{label}</div>
      <div>
        {row.healed} healed · {row.escalated} escalated
      </div>
    </div>
  )
}

// One row per drift class, segmented healed (pass) vs escalated (fail).
// Every class is always shown, even with zero attempts (spec.nullability is
// "partial" per the known limitations) — an absent row reads as a bug.
export default function DriftDistribution({ data }) {
  // Same fix as SpecialistPerformance: a zero-width escalated SEGMENT gives
  // Recharts nowhere to anchor the count LabelList, so it silently doesn't
  // render — not just when the whole row is empty (nullability, count=0),
  // but any time escalated=0 on its own, healed>0 or not (e.g. "type":
  // healed=7, escalated=0 — the label that should show "7" went missing
  // for the exact same reason, just with the other segment at zero).
  const rows = data.map((d) => ({
    ...d,
    escalated_display: d.escalated === 0 ? 0.03 : d.escalated,
  }))

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        Drift classes
      </h3>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 32, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--color-line)" strokeOpacity={0.6} horizontal={false} />
          {/* Visible tick labels, not just gridlines — lets a reader check
              a bar's actual length against the scale instead of eyeballing
              one bar's proportion against another's. */}
          <XAxis
            type="number"
            allowDecimals={false}
            tick={AXIS_TICK}
            axisLine={{ stroke: 'var(--color-line)' }}
            tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="drift_class"
            tick={AXIS_TICK}
            axisLine={{ stroke: 'var(--color-line)' }}
            tickLine={false}
            width={80}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: 'var(--color-surface-raised)' }} />
          <Bar dataKey="healed" stackId="a" fill="var(--color-pass)" radius={[2, 0, 0, 2]} barSize={16} />
          <Bar dataKey="escalated_display" stackId="a" fill="var(--color-fail)" radius={[0, 2, 2, 0]} barSize={16}>
            <LabelList dataKey="count" position="right" fill="var(--color-ink-muted)" fontSize={11} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
