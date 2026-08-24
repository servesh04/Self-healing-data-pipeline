import { useRunStore } from '../store/useRunStore'
import GraphCanvas from '../components/GraphCanvas/GraphCanvas'
import AssertionBars from '../components/AssertionBars/AssertionBars'
import SchemaDiff from '../components/SchemaDiff/SchemaDiff'
import PatchDiff from '../components/PatchDiff/PatchDiff'
import ApprovalPanel from '../components/ApprovalPanel/ApprovalPanel'
import MappingAudit from '../components/MappingAudit/MappingAudit'
import AttemptTimeline from '../components/AttemptTimeline/AttemptTimeline'

const STATUS_BADGE = {
  clean: 'border-emerald-600 bg-emerald-950/60 text-emerald-300',
  healed: 'border-emerald-600 bg-emerald-950/60 text-emerald-300',
  escalated: 'border-red-700 bg-red-950/60 text-red-300',
  awaiting_approval: 'border-amber-600 bg-amber-950/60 text-amber-300',
  running: 'border-sky-600 bg-sky-950/60 text-sky-300',
}

export default function RunDetail() {
  const runId = useRunStore((s) => s.selectedRunId)
  const detail = useRunStore((s) => s.detail)
  const detailError = useRunStore((s) => s.detailError)
  const clearSelection = useRunStore((s) => s.clearSelection)

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={clearSelection}
          className="rounded border border-neutral-700 px-2 py-1 text-xs text-neutral-400 hover:border-neutral-500 hover:text-neutral-200"
        >
          ← runs
        </button>
        <span className="truncate font-mono text-xs text-neutral-500">{runId}</span>
        {detail && (
          <span
            className={`ml-auto shrink-0 rounded-full border px-2.5 py-0.5 font-mono text-[11px] uppercase ${
              STATUS_BADGE[detail.api_status] ?? 'border-neutral-700 text-neutral-400'
            }`}
          >
            {detail.api_status}
          </span>
        )}
      </div>

      {detailError && (
        <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-300">
          {detailError}
        </div>
      )}

      {detail?.source_path && (
        <p className="font-mono text-xs text-neutral-600">
          {detail.source_path.split(/[\\/]/).pop()}
          {detail.final_message && (
            <span className="ml-2 text-neutral-400">— {detail.final_message}</span>
          )}
        </p>
      )}

      {/* GraphCanvas first, full width — the heal-attempt badge is the single
          most important pixel in the project (per project direction);
          everything else is supporting detail underneath it. */}
      <GraphCanvas detail={detail} />

      <ApprovalPanel detail={detail} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <AssertionBars detail={detail} />
        <SchemaDiff detail={detail} />
        <PatchDiff detail={detail} />
        <MappingAudit detail={detail} />
      </div>

      <AttemptTimeline detail={detail} />
    </div>
  )
}
