import { useRunStore } from '../store/useRunStore'
import GraphCanvas from '../components/GraphCanvas/GraphCanvas'
import AssertionBars from '../components/AssertionBars/AssertionBars'
import SchemaDiff from '../components/SchemaDiff/SchemaDiff'
import PatchDiff from '../components/PatchDiff/PatchDiff'
import ApprovalPanel from '../components/ApprovalPanel/ApprovalPanel'
import MappingAudit from '../components/MappingAudit/MappingAudit'
import AttemptTimeline from '../components/AttemptTimeline/AttemptTimeline'
import RunChat from '../components/RunChat/RunChat'
import { runStatusChip } from '../lib/statusStyles'

const PAGE_LABEL = { overview: 'Overview', runs: 'Runs', mapping: 'Mapping' }

export default function RunDetail() {
  const runId = useRunStore((s) => s.selectedRunId)
  const detail = useRunStore((s) => s.detail)
  const detailError = useRunStore((s) => s.detailError)
  const clearSelection = useRunStore((s) => s.clearSelection)
  const currentPage = useRunStore((s) => s.currentPage)

  // HealState has no top-level `approved_by` — only per-attempt, inside
  // `history` — same reason PatchDiff sources confidence/drift_class from
  // there instead of a top-level field (Phase 5).
  const lastAttempt = detail?.history?.[detail.history.length - 1]
  const chip = detail ? runStatusChip(detail.api_status, lastAttempt?.approved_by) : null

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs">
        <button onClick={clearSelection} className="text-ink-muted hover:text-ink">
          {PAGE_LABEL[currentPage] ?? 'Overview'}
        </button>
        <span className="text-ink-faint">/</span>
        <span className="truncate font-mono text-ink-muted">{runId}</span>
        {chip && (
          <span className={`ml-auto shrink-0 rounded-full border px-2.5 py-0.5 font-mono text-[11px] ${chip.className}`}>
            {chip.label}
          </span>
        )}
      </div>

      {detailError && (
        <div className="rounded border border-fail bg-fail/10 px-3 py-2 text-xs text-fail">
          {detailError}
        </div>
      )}

      {detail?.source_path && (
        <p className="font-mono text-xs text-ink-faint">
          {detail.source_ref?.source_id ?? detail.source_path.split(/[\\/]/).pop()}
          <span className="ml-2 rounded border border-line-strong px-1.5 py-0.5 text-[10px] text-ink-muted">
            {detail.contract_name || 'default'} · {detail.contract_version?.slice(0, 8) || 'legacy'}
          </span>
          {detail.mapping_scope && <span className="ml-2 text-[10px] text-ink-faint">{detail.mapping_scope}</span>}
          {detail.final_message && (
            <span className="ml-2 text-ink-muted">— {detail.final_message}</span>
          )}
        </p>
      )}
      {detail?.rollback_reason && (
        <p className="rounded border border-fail/50 bg-fail/10 px-3 py-2 text-xs text-fail">
          Mapping rollback #{detail.mapping_version_written} → #{detail.rollback_version}: {detail.rollback_reason}
        </p>
      )}
      {detail?.audit_error && <p className="text-xs text-fail">Audit warning: {detail.audit_error}</p>}
      {detail?.distribution_alerts?.length > 0 && (
        <div className="rounded border border-signal bg-signal/10 px-3 py-2 text-xs text-signal">
          Distribution alert against {detail.distribution_baseline_id} · {detail.distribution_baseline_version}
          {detail.distribution_alerts.map((alert) => (
            <p key={alert.column} className="mt-1">{alert.column}: median {alert.observed_median.toFixed(2)} versus {alert.baseline_median.toFixed(2)} ({alert.distance_iqr.toFixed(2)} baseline IQRs)</p>
          ))}
        </div>
      )}
      {detail?.distribution_skipped?.some((item) => item.reason === 'insufficient sample') && (
        <p className="text-xs text-ink-muted">Distribution check skipped: sample below the monitoring minimum.</p>
      )}

      {/* GraphCanvas takes the remaining width; supporting detail moves into
          a fixed 380px sidebar (DASHBOARD.md's only two run-detail layout
          changes). The heal-attempt badge is still the single most
          important pixel in the project — it just has more room now. */}
      <div className="flex flex-col gap-4 lg:flex-row">
        <div className="min-w-0 flex-1">
          <GraphCanvas detail={detail} />
          <div className="mt-4">
            <AttemptTimeline detail={detail} />
          </div>
        </div>
        <div className="flex w-full flex-col gap-4 lg:w-[380px] lg:shrink-0">
          <ApprovalPanel detail={detail} />
          {detail?.approval_history?.length > 0 && (
            <details className="rounded-lg border border-line bg-surface">
              <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">Approval history</summary>
              <div className="space-y-2 border-t border-line p-3 text-xs text-ink-muted">
                {detail.approval_history.map((item) => (
                  <p key={item.attempt_no}>Attempt {item.attempt_no}: {item.decision} · {item.final_status ?? 'pending'}
                    {item.note && <span className="block text-ink-faint">{item.note}</span>}
                  </p>
                ))}
              </div>
            </details>
          )}
          <AssertionBars detail={detail} />
          <SchemaDiff detail={detail} />
          <PatchDiff detail={detail} />
          {detail?.mapping_audit?.length > 0 && (
            <details className="rounded-lg border border-line bg-surface">
              <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
                Mapping audit trail
              </summary>
              <div className="border-t border-line p-3 pt-2">
                <MappingAudit detail={detail} />
              </div>
            </details>
          )}
          {/* Read-only chat over this run's data — new feature, no
              relation to the healing graph. Open by default: unlike
              mapping audit (supplementary), this is the thing worth
              surfacing immediately. */}
          {detail && (
            <details open className="rounded-lg border border-line bg-surface">
              <summary className="cursor-pointer px-3 py-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
                Ask about this run
              </summary>
              <div className="border-t border-line p-3 pt-2">
                <RunChat runId={runId} />
              </div>
            </details>
          )}
        </div>
      </div>
    </div>
  )
}
