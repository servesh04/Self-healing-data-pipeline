// Fixed topology — ARCHITECTURE.md's "Graph Topology" section, hand-drawn as
// coordinates rather than laid out by a library. The layout is fixed and
// known ahead of time (ARCHITECTURE.md: "Hand-roll the SVG... Do not add
// React Flow; it is a dependency and a time sink for zero extra marks"), so
// there is nothing here that needs a real layout engine.

export const VIEW_W = 1000
export const VIEW_H = 860
export const NODE_W = 148
export const NODE_H = 46

export const NODES = [
  { id: 'run_pipeline', label: 'run_pipeline', x: 500, y: 40, kind: 'process' },
  { id: 'commit_report', label: 'commit_report', x: 160, y: 150, kind: 'terminal' },
  { id: 'profile_source', label: 'profile_source', x: 500, y: 150, kind: 'process' },
  { id: 'diff_contract', label: 'diff_contract', x: 500, y: 240, kind: 'process' },
  { id: 'diagnose', label: 'diagnose', x: 500, y: 330, kind: 'llm' },
  { id: 'spec_rename', label: 'spec_rename', x: 260, y: 420, kind: 'llm' },
  { id: 'spec_type', label: 'spec_type', x: 500, y: 420, kind: 'llm' },
  { id: 'spec_nullability', label: 'spec_nullability', x: 740, y: 420, kind: 'llm' },
  { id: 'propose_patch', label: 'propose_patch', x: 500, y: 510, kind: 'llm' },
  { id: 'human_approval', label: 'human_approval', x: 760, y: 600, kind: 'gate' },
  { id: 'apply_patch', label: 'apply_patch', x: 500, y: 600, kind: 'process' },
  { id: 'rerun_validate', label: 'rerun_validate', x: 500, y: 690, kind: 'process' },
  { id: 'escalate', label: 'escalate', x: 840, y: 790, kind: 'terminal' },
]

export const NODE_BY_ID = Object.fromEntries(NODES.map((n) => [n.id, n]))

// type: 'straight' | 'curve' (a gentle side arc) | 'loop' (the heal cycle —
// drawn distinctly and highlighted whenever heal_attempts > 0)
export const EDGES = [
  { from: 'run_pipeline', to: 'profile_source', type: 'straight' },
  { from: 'run_pipeline', to: 'commit_report', type: 'curve', label: 'pass', side: 'left' },
  { from: 'profile_source', to: 'diff_contract', type: 'straight' },
  { from: 'diff_contract', to: 'diagnose', type: 'straight' },
  { from: 'diagnose', to: 'spec_rename', type: 'straight' },
  { from: 'diagnose', to: 'spec_type', type: 'straight' },
  { from: 'diagnose', to: 'spec_nullability', type: 'straight' },
  { from: 'diagnose', to: 'escalate', type: 'curve', label: 'unknown', side: 'right' },
  { from: 'spec_rename', to: 'propose_patch', type: 'straight' },
  { from: 'spec_type', to: 'propose_patch', type: 'straight' },
  { from: 'spec_nullability', to: 'propose_patch', type: 'straight' },
  { from: 'propose_patch', to: 'apply_patch', type: 'straight', label: 'high' },
  { from: 'propose_patch', to: 'human_approval', type: 'straight', label: 'low' },
  { from: 'human_approval', to: 'apply_patch', type: 'straight', label: 'approve' },
  { from: 'human_approval', to: 'escalate', type: 'straight', label: 'reject' },
  { from: 'apply_patch', to: 'rerun_validate', type: 'straight' },
  { from: 'rerun_validate', to: 'commit_report', type: 'curve', label: 'pass', side: 'left' },
  { from: 'rerun_validate', to: 'diff_contract', type: 'loop', label: 'fail, < 3' },
  { from: 'rerun_validate', to: 'escalate', type: 'curve', label: 'cap', side: 'right' },
]

/** Which nodes were visited at least once, inferred from the run's current
 * state — the REST-polled snapshot has no per-node event stream (Poll rather
 * than WebSocket; ARCHITECTURE.md notes SSE as a known limitation), so
 * "visited" is the honest granularity available, not "currently executing".
 */
export function deriveVisited(detail) {
  const visited = new Set(['run_pipeline'])
  if (!detail) return visited

  const everDrifted =
    detail.heal_attempts > 0 ||
    (detail.drift_items && detail.drift_items.length > 0) ||
    detail.drift_class != null ||
    detail.status !== 'clean'

  if (everDrifted && detail.status !== 'clean') {
    visited.add('profile_source')
    visited.add('diff_contract')
  }
  if (detail.drift_class) {
    visited.add('diagnose')
    if (['rename', 'type', 'nullability'].includes(detail.drift_class)) {
      visited.add(`spec_${detail.drift_class}`)
      visited.add('propose_patch')
    }
  }
  if (detail.heal_attempts > 0 || detail.applied_patch) {
    visited.add('apply_patch')
    visited.add('rerun_validate')
  }
  if (detail.awaiting_approval || detail.human_decision) {
    visited.add('human_approval')
  }
  if (detail.status === 'healed' || detail.status === 'clean') {
    visited.add('commit_report')
  }
  if (detail.status === 'escalated') {
    visited.add('escalate')
  }
  // Every completed heal attempt proves at least one more lap through the
  // cycle happened, even once the run has moved past it.
  if (detail.history && detail.history.length > 1) {
    visited.add('diff_contract')
    visited.add('diagnose')
  }
  return visited
}

/** The node the run is presently sitting at, if that's actually knowable
 * from a snapshot alone — only true for the interrupt (`next` tells us
 * exactly). Anything else while status is "running" is genuinely unknown at
 * 1s polling resolution, and the UI should say "in progress", not guess.
 */
export function deriveActiveNode(detail) {
  if (!detail) return null
  if (detail.next && detail.next.includes('human_approval')) return 'human_approval'
  return null
}
