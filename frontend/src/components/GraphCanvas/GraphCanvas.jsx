import { useMemo } from 'react'
import {
  VIEW_W,
  VIEW_H,
  NODE_W,
  NODE_H,
  NODES,
  NODE_BY_ID,
  EDGES,
  deriveVisited,
  deriveActiveNode,
} from './topology'

// ── Edge path geometry ───────────────────────────────────────────────────
// Hand-drawn, not layout-engine-computed — the topology is fixed and known
// (ARCHITECTURE.md: "Hand-roll the SVG... Do not add React Flow").

function straightPath(from, to) {
  // Enter/exit from whichever face points toward the other node, so a
  // diagonal hop (e.g. diagnose -> spec_rename) doesn't visually cut through
  // the box corners.
  const dx = to.x - from.x
  const dy = to.y - from.y
  const steepness = Math.abs(dy) / (Math.abs(dx) || 1)
  const fx = from.x + (steepness > 1 ? 0 : Math.sign(dx) * NODE_W * 0.3)
  const fy = from.y + (steepness > 1 ? Math.sign(dy) * NODE_H * 0.5 : Math.sign(dy) * NODE_H * 0.3)
  const tx = to.x - (steepness > 1 ? 0 : Math.sign(dx) * NODE_W * 0.3)
  const ty = to.y - (steepness > 1 ? Math.sign(dy) * NODE_H * 0.5 : Math.sign(dy) * NODE_H * 0.3)
  return { d: `M ${fx} ${fy} L ${tx} ${ty}`, labelX: (fx + tx) / 2, labelY: (fy + ty) / 2 }
}

function curvePath(from, to, side) {
  const sign = side === 'left' ? -1 : 1
  const midX = (from.x + to.x) / 2
  const midY = (from.y + to.y) / 2
  const bulge = 90 * sign
  const fy = from.y + Math.sign(to.y - from.y) * NODE_H * 0.5
  const ty = to.y - Math.sign(to.y - from.y) * NODE_H * 0.5
  return {
    d: `M ${from.x} ${fy} Q ${midX + bulge} ${midY} ${to.x} ${ty}`,
    labelX: midX + bulge * 0.7,
    labelY: midY,
  }
}

function loopPath(from, to) {
  // 410, not a smaller offset: spec_rename sits at x=260 (spans [186, 334]),
  // and the heal-attempt badge is centered on this path's midpoint — too
  // small a clearance here means the single most important element on the
  // canvas visually collides with a node box.
  const loopX = Math.min(from.x, to.x) - 410
  const fx = from.x - NODE_W / 2
  const tx = to.x - NODE_W / 2
  return {
    d: `M ${fx} ${from.y} C ${loopX} ${from.y}, ${loopX} ${to.y}, ${tx} ${to.y}`,
    labelX: loopX,
    labelY: (from.y + to.y) / 2,
  }
}

function edgeGeometry(edge) {
  const from = NODE_BY_ID[edge.from]
  const to = NODE_BY_ID[edge.to]
  if (edge.type === 'loop') return loopPath(from, to)
  if (edge.type === 'curve') return curvePath(from, to, edge.side)
  return straightPath(from, to)
}

// ── Status colors ────────────────────────────────────────────────────────
const STATUS_STYLE = {
  idle: { fill: 'var(--gc-idle-fill)', stroke: 'var(--gc-idle-stroke)', text: 'var(--gc-idle-text)' },
  done: { fill: 'var(--gc-done-fill)', stroke: 'var(--gc-done-stroke)', text: 'var(--gc-done-text)' },
  active: { fill: 'var(--gc-active-fill)', stroke: 'var(--gc-active-stroke)', text: 'var(--gc-active-text)' },
}

function nodeStatus(id, visited, activeNode) {
  if (id === activeNode) return 'active'
  if (visited.has(id)) return 'done'
  return 'idle'
}

export default function GraphCanvas({ detail }) {
  const visited = useMemo(() => deriveVisited(detail), [detail])
  const activeNode = useMemo(() => deriveActiveNode(detail), [detail])
  const healAttempts = detail?.heal_attempts ?? 0
  const cycling = healAttempts > 0 && detail?.status === 'running'
  const loopEdge = EDGES.find((e) => e.type === 'loop')
  const loopGeom = edgeGeometry(loopEdge)

  return (
    <div className="gc-root rounded-lg border border-line bg-surface p-3">
      <style>{`
        :root {
          --gc-idle-fill: var(--color-surface); --gc-idle-stroke: var(--color-line-strong); --gc-idle-text: var(--color-ink-faint);
          --gc-done-fill: color-mix(in srgb, var(--color-pass) 18%, var(--color-surface)); --gc-done-stroke: var(--color-pass); --gc-done-text: var(--color-pass);
          --gc-active-fill: color-mix(in srgb, var(--color-signal) 22%, var(--color-surface)); --gc-active-stroke: var(--color-signal); --gc-active-text: var(--color-signal);
          --gc-edge: var(--color-line-strong); --gc-edge-active: var(--color-pass); --gc-loop: var(--color-signal);
          --gc-label: var(--color-ink-faint);
        }
        .gc-node-active rect { animation: gcPulse 1.4s ease-in-out infinite; }
        @keyframes gcPulse {
          0%, 100% { filter: drop-shadow(0 0 0px var(--gc-active-stroke)); }
          50% { filter: drop-shadow(0 0 8px var(--gc-active-stroke)); }
        }
        .gc-loop-path { stroke-dasharray: 8 6; }
        .gc-loop-path.cycling { animation: gcDash 0.6s linear infinite; }
        @keyframes gcDash { to { stroke-dashoffset: -28; } }
        .gc-badge { animation: gcBadgeIn 0.4s cubic-bezier(0.34, 1.56, 0.64, 1); }
        @keyframes gcBadgeIn {
          0% { transform: scale(0.6); opacity: 0; }
          60% { transform: scale(1.12); opacity: 1; }
          100% { transform: scale(1); opacity: 1; }
        }
      `}</style>

      <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="w-full" style={{ minHeight: 520 }}>
        <defs>
          <marker id="gc-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--gc-edge)" />
          </marker>
          <marker id="gc-arrow-loop" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--gc-loop)" />
          </marker>
        </defs>

        {/* edges */}
        {EDGES.map((edge) => {
          const geom = edgeGeometry(edge)
          const isLoop = edge.type === 'loop'
          const bothVisited = visited.has(edge.from) && visited.has(edge.to)
          return (
            <g key={`${edge.from}->${edge.to}`}>
              <path
                d={geom.d}
                fill="none"
                stroke={isLoop ? 'var(--gc-loop)' : bothVisited ? 'var(--gc-edge-active)' : 'var(--gc-edge)'}
                strokeWidth={isLoop ? 3 : bothVisited ? 2.5 : 1.5}
                className={isLoop ? `gc-loop-path ${cycling ? 'cycling' : ''}` : ''}
                markerEnd={`url(#${isLoop ? 'gc-arrow-loop' : 'gc-arrow'})`}
                opacity={isLoop && healAttempts === 0 ? 0.35 : 1}
              />
              {edge.label && (
                <text
                  x={geom.labelX}
                  y={geom.labelY}
                  fill="var(--gc-label)"
                  fontSize="11"
                  textAnchor="middle"
                  className="select-none"
                >
                  {edge.label}
                </text>
              )}
            </g>
          )
        })}

        {/* nodes */}
        {NODES.map((node) => {
          const status = nodeStatus(node.id, visited, activeNode)
          const style = STATUS_STYLE[status]
          return (
            <g
              key={node.id}
              transform={`translate(${node.x - NODE_W / 2}, ${node.y - NODE_H / 2})`}
              className={status === 'active' ? 'gc-node-active' : ''}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                fill={style.fill}
                stroke={style.stroke}
                strokeWidth={status === 'idle' ? 1 : 2}
              />
              <text
                x={NODE_W / 2}
                y={NODE_H / 2 + 4}
                fill={style.text}
                fontSize="12.5"
                fontWeight={status === 'idle' ? 400 : 600}
                textAnchor="middle"
                className="select-none"
              >
                {node.label}
              </text>
            </g>
          )
        })}

        {/* THE heal-attempt badge — the single most important pixel here */}
        {healAttempts > 0 && (
          <g key={healAttempts} className="gc-badge" transform={`translate(${loopGeom.labelX}, ${loopGeom.labelY - 20})`}>
            <rect
              x={-72}
              y={-20}
              width={144}
              height={40}
              rx={20}
              fill="var(--color-signal)"
              stroke="var(--color-signal-dim)"
              strokeWidth={2}
            />
            <text
              x={0}
              y={6}
              textAnchor="middle"
              fontSize="16"
              fontWeight="700"
              fill="var(--color-canvas)"
            >
              {'↺'} Heal attempt {healAttempts}
            </text>
          </g>
        )}
      </svg>

      <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-ink-muted">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full border border-line-strong bg-surface-raised" /> idle
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full border border-pass bg-pass/20" /> visited
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-full border border-signal bg-signal/20" /> paused here
        </span>
        <span className="ml-auto italic text-ink-faint">
          node status reflects the polled snapshot (2s interval) — not a live per-node stream
        </span>
      </div>
    </div>
  )
}
