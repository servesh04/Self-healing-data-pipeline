import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

const AXIS_TICK = { fill: 'var(--color-ink-faint)', fontSize: 11, fontFamily: 'IBM Plex Mono, monospace' }
const AXIS_LINE = { stroke: 'var(--color-line)' }

// Deterministic per-point jitter (not Math.random()) so points don't jump
// around on every poll — same run+attempt always lands at the same offset.
function jitter(key) {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0
  return ((h % 100) / 100 - 0.5) * 0.5
}

function outcomeColor(row) {
  if (row.approved_by === 'human') return 'var(--color-signal)'
  return row.result === 'passed' ? 'var(--color-pass)' : 'var(--color-fail)'
}

// `kind: "attempt"` is a real HealAttempt — round dot. `unclassified`
// (day5-style: diagnose never found a viable class, no patch was ever on
// the table) gets a distinct diamond, since it's a fundamentally different
// event from an attempt that ran and failed. `rejected` (a human declined a
// real proposed patch) stays a round dot — a patch WAS on the table, a
// decision was just made on it — just fail-coloured like any other
// non-passed outcome (outcomeColor already handles that with no special
// case needed, since its result is never "passed").
function renderPoint(props) {
  const { cx, cy, payload } = props
  const color = outcomeColor(payload)
  if (payload.kind === 'unclassified') {
    const s = 5.5
    return (
      <path
        d={`M ${cx} ${cy - s} L ${cx + s} ${cy} L ${cx} ${cy + s} L ${cx - s} ${cy} Z`}
        fill={color}
        stroke="var(--color-canvas)"
        strokeWidth={1}
      />
    )
  }
  return <circle cx={cx} cy={cy} r={4} fill={color} />
}

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div className="rounded border border-line-strong bg-surface-raised px-2.5 py-1.5 font-mono text-[11px] text-ink shadow-lg">
      <div className="text-ink-muted">
        {p.kind === 'attempt' ? `${p.specialist} · attempt ${p.attempt_no}` : `drift_class: ${p.drift_class ?? 'unknown'}`}
      </div>
      <div>confidence {p.confidence?.toFixed(2)}</div>
      <div className="text-ink-muted">
        {p.kind === 'unclassified' ? 'escalated — no viable class found' : p.result}
        {p.approved_by ? ` · via ${p.approved_by}` : ''}
      </div>
    </div>
  )
}

// x = confidence (0-1), y = attempt number (jittered so same-attempt points
// don't stack exactly on top of each other; runs with no HealAttempt at
// all — unclassified or rejected escalations — sit at y=0, their own band).
// This visualises exactly what Phase 3 tuned — that CONFIDENCE_THRESHOLD
// actually separates auto-heals from escalations/human-review cases.
export default function ConfidenceChart({ data }) {
  const points = data.map((d) => ({ ...d, y: d.attempt_no + jitter(`${d.run_id}:${d.attempt_no}`) }))

  return (
    <div className="rounded-lg border border-line bg-surface p-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
        Confidence calibration
      </h3>
      {points.length === 0 ? (
        <p className="text-xs text-ink-faint">no heal attempts yet</p>
      ) : (
        <>
          <ResponsiveContainer width="100%" height={220}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 0, left: -12 }}>
              <CartesianGrid stroke="var(--color-line)" strokeOpacity={0.6} />
              <XAxis
                type="number"
                dataKey="confidence"
                domain={[0, 1]}
                tick={AXIS_TICK}
                axisLine={AXIS_LINE}
                tickLine={AXIS_LINE}
              />
              <YAxis
                type="number"
                dataKey="y"
                tick={AXIS_TICK}
                axisLine={AXIS_LINE}
                tickLine={AXIS_LINE}
                allowDecimals={false}
                width={28}
                label={{ value: 'attempt #', angle: -90, position: 'insideLeft', fill: 'var(--color-ink-faint)', fontSize: 11 }}
              />
              <ReferenceLine
                x={0.75}
                stroke="var(--color-signal)"
                strokeDasharray="4 4"
                label={{
                  value: 'CONFIDENCE_THRESHOLD',
                  position: 'insideTopRight',
                  fill: 'var(--color-signal)',
                  fontSize: 10,
                }}
              />
              <Tooltip content={<CustomTooltip />} cursor={{ stroke: 'var(--color-line-strong)' }} />
              <Scatter data={points} shape={renderPoint} />
            </ScatterChart>
          </ResponsiveContainer>
          <div className="mt-1 flex flex-wrap gap-4 text-[11px] text-ink-faint">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-full bg-pass" /> passed
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-full bg-fail" /> failed / rejected
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2 w-2 rounded-full bg-signal" /> human-approved
            </span>
            <span className="flex items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-2.5 bg-fail"
                style={{ clipPath: 'polygon(50% 0%, 100% 50%, 50% 100%, 0% 50%)' }}
              />
              no viable class (unclassified)
            </span>
          </div>
        </>
      )}
    </div>
  )
}
