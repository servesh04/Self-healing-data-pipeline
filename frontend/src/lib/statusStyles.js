// Centralized status → className maps, built on the design tokens declared
// in index.css (--pass/--fail/--signal/--running/--idle). Consolidated from
// what used to be 3 near-identical copies (RunList.jsx, RunDetail.jsx,
// AttemptTimeline.jsx) plus SchemaDiff's own map — this is presentation
// only, which status maps to which color, not what data produces a status.

// DASHBOARD.md's status-chip mapping — one colour per meaning, no
// exceptions. `healed` needs `approved_by` too: a human-approved heal gets
// the same pass colour plus a left border in signal, so "a human was
// involved" reads at a glance without a second color competing with pass/fail.
export function runStatusChip(status, approvedBy) {
  switch (status) {
    case 'clean':
      return { className: 'border-line-strong text-ink-muted', label: 'CLEAN' }
    case 'healed':
      return approvedBy === 'human'
        ? { className: 'border-pass border-l-2 border-l-signal text-pass', label: 'HEALED · APPROVED' }
        : { className: 'border-pass text-pass', label: 'HEALED' }
    case 'escalated':
      return { className: 'border-fail text-fail', label: 'ESCALATED' }
    case 'awaiting_approval':
      return { className: 'animate-pulse border-signal text-signal', label: 'AWAITING' }
    case 'running':
      return { className: 'border-running text-running', label: 'RUNNING' }
    default:
      return { className: 'border-line-strong text-ink-faint', label: (status ?? 'unknown').toUpperCase() }
  }
}

// Legacy plain map, kept for spots that only ever show `status` with no
// `approved_by` context (e.g. before a run has any history at all).
export const RUN_STATUS_BADGE = {
  clean: 'border-line-strong text-ink-muted',
  healed: 'border-pass bg-pass/15 text-pass',
  escalated: 'border-fail bg-fail/15 text-fail',
  awaiting_approval: 'border-signal bg-signal/15 text-signal',
  running: 'border-running bg-running/15 text-running',
}
export const RUN_STATUS_FALLBACK = 'border-line-strong text-ink-faint'

export const ATTEMPT_RESULT_STYLE = {
  passed: 'border-pass text-pass',
  failed: 'border-fail text-fail',
  rejected: 'border-line-strong text-ink-faint',
}
export const ATTEMPT_RESULT_FALLBACK = 'border-line-strong text-ink-faint'

// `missing`/`mismatch` both read as broken (fail); `unexpected` reads as
// "something here is worth a look" (signal) rather than a hard failure — an
// extra column isn't necessarily wrong, just worth noticing.
export const SCHEMA_STATUS_STYLE = {
  missing: 'border-fail bg-fail/15 text-fail',
  unexpected: 'border-signal bg-signal/15 text-signal',
  mismatch: 'border-fail bg-fail/15 text-fail',
  matching: 'border-pass bg-pass/10 text-pass',
}
export const SCHEMA_STATUS_LABEL = {
  missing: 'missing',
  unexpected: 'unexpected',
  mismatch: 'type mismatch',
  matching: 'matching',
}
