import { create } from 'zustand'
import * as api from '../api/client'

const ACTIVE_STATUSES = new Set(['running', 'awaiting_approval'])
// ARCHITECTURE.md: "dashboard polls run state" — 2s rather than the 1s
// implied elsewhere: a real checkpoint read against Neon (through the
// checkpointer's own serializing lock) has been observed to take ~1-2s, so a
// 1s interval mostly overlaps in-flight requests rather than getting fresher
// data. Nothing in this pipeline changes state faster than an LLM call
// completes anyway (seconds, not sub-second), so this loses no responsiveness
// a viewer would notice.
const POLL_MS = 2000

let pollHandle = null

export const useRunStore = create((set, get) => ({
  // ── Navigation — no router; 3 base pages plus a run-detail overlay that
  // remembers which page to return to (DASHBOARD.md's app shell nav). ──
  currentPage: 'overview', // 'overview' | 'runs' | 'mapping'
  contractName: 'default',
  scopeRevision: 0,
  contracts: [],
  sources: [],
  async fetchContracts() {
    try { const { contracts } = await api.getContracts(); set({ contracts }) } catch { /* keep default */ }
  },
  async fetchSources() {
    const contractName = get().contractName
    const revision = get().scopeRevision
    try {
      const { sources } = await api.getSources(contractName)
      if (get().scopeRevision === revision) set({ sources })
    } catch {
      if (get().scopeRevision === revision) set({ sources: [] })
    }
  },
  setContractName(contractName) {
    get().stopPolling()
    set((s) => ({ contractName, scopeRevision: s.scopeRevision + 1,
      runs: [], overviewRuns: [], mappingHistory: [], sources: [],
      selectedRunId: null, detail: null,
      analytics: { ...s.analytics, summary: null, confidence: [], driftDistribution: [],
        specialistPerformance: [], componentMetrics: null, error: null } }))
    get().fetchRuns()
    get().fetchSources()
    get().fetchOverviewRuns()
    get().fetchAnalytics()
    get().fetchMappingHistory()
  },

  runs: [],
  runsLoading: false,
  runsError: null,
  runsFilter: { status: null, dataset: null },

  overviewRuns: [],
  overviewRunsLoading: false,

  selectedRunId: null,
  detail: null,
  detailError: null,

  triggering: false,
  triggerError: null,
  approving: false,

  newRunOpen: false,

  analytics: {
    summary: null,
    confidence: [],
    driftDistribution: [],
    specialistPerformance: [],
    componentMetrics: null,
    loading: false,
    error: null,
  },

  mappingHistory: [],
  mappingHistoryLoading: false,
  mappingHistoryError: null,

  goToPage(page) {
    get().stopPolling()
    set({ currentPage: page, selectedRunId: null, detail: null, detailError: null })
  },

  openNewRun() {
    set({ newRunOpen: true, triggerError: null })
  },
  closeNewRun() {
    set({ newRunOpen: false })
  },

  async fetchRuns(filterOverride) {
    const filter = filterOverride ?? get().runsFilter
    const contractName = get().contractName
    const revision = get().scopeRevision
    set({ runsLoading: true, runsError: null, runsFilter: filter })
    try {
      const { runs } = await api.listRuns({ limit: 200, status: filter.status, dataset: filter.dataset, contractName })
      if (get().scopeRevision === revision) set({ runs, runsLoading: false })
    } catch (err) {
      if (get().scopeRevision === revision) set({ runsError: err.message, runsLoading: false })
    }
  },

  async fetchOverviewRuns() {
    const contractName = get().contractName
    const revision = get().scopeRevision
    set({ overviewRunsLoading: true })
    try {
      const { runs } = await api.listRuns({ limit: 14, contractName })
      if (get().scopeRevision === revision) set({ overviewRuns: runs, overviewRunsLoading: false })
    } catch {
      if (get().scopeRevision === revision) set({ overviewRunsLoading: false })
    }
  },

  async fetchAnalytics() {
    const contractName = get().contractName
    const revision = get().scopeRevision
    set((s) => ({ analytics: { ...s.analytics, loading: true, error: null } }))
    try {
      const [summary, confidence, driftDistribution, specialistPerformance, componentMetrics] = await Promise.all([
        api.getAnalyticsSummary(contractName),
        api.getAnalyticsConfidence(contractName),
        api.getAnalyticsDriftDistribution(contractName),
        api.getAnalyticsSpecialistPerformance(contractName),
        api.getComponentMetrics(contractName),
      ])
      if (get().scopeRevision === revision) set({ analytics: { summary, confidence, driftDistribution, specialistPerformance, componentMetrics, loading: false, error: null } })
    } catch (err) {
      if (get().scopeRevision === revision) set((s) => ({ analytics: { ...s.analytics, loading: false, error: err.message } }))
    }
  },

  async fetchMappingHistory() {
    const contractName = get().contractName
    const revision = get().scopeRevision
    set({ mappingHistoryLoading: true, mappingHistoryError: null })
    try {
      const { history } = await api.getMappingHistory(100, contractName)
      if (get().scopeRevision === revision) set({ mappingHistory: history, mappingHistoryLoading: false })
    } catch (err) {
      if (get().scopeRevision === revision) set({ mappingHistoryError: err.message, mappingHistoryLoading: false })
    }
  },

  async selectRun(runId) {
    get().stopPolling()
    set({ selectedRunId: runId, detail: null, detailError: null })
    await get().refreshDetail()
    get().startPolling()
  },

  clearSelection() {
    get().stopPolling()
    set({ selectedRunId: null, detail: null, detailError: null })
  },

  async refreshDetail() {
    const { selectedRunId } = get()
    if (!selectedRunId) return
    try {
      const detail = await api.getRun(selectedRunId)
      // A poll racing a selection change would otherwise clobber the newer
      // selection with a stale response for the old one.
      if (get().selectedRunId === selectedRunId) {
        set({ detail, detailError: null })
        if (!ACTIVE_STATUSES.has(detail.api_status)) get().stopPolling()
      }
    } catch (err) {
      if (get().selectedRunId === selectedRunId) {
        set({ detailError: err.message })
      }
    }
  },

  startPolling() {
    if (pollHandle) return
    pollHandle = setInterval(() => get().refreshDetail(), POLL_MS)
  },

  stopPolling() {
    if (pollHandle) {
      clearInterval(pollHandle)
      pollHandle = null
    }
  },

  async trigger(sourcePath) {
    set({ triggering: true, triggerError: null })
    try {
      const { run_id } = await api.triggerRun(sourcePath, get().contractName)
      set({ triggering: false, newRunOpen: false })
      await get().selectRun(run_id)
      return run_id
    } catch (err) {
      set({ triggering: false, triggerError: err.message })
      throw err
    }
  },

  async approve(note) {
    const { selectedRunId } = get()
    if (!selectedRunId) return
    set({ approving: true })
    try {
      await api.approveRun(selectedRunId, note)
      await get().refreshDetail()
      get().startPolling()
    } finally {
      set({ approving: false })
    }
  },

  async reject(note) {
    const { selectedRunId } = get()
    if (!selectedRunId) return
    set({ approving: true })
    try {
      await api.rejectRun(selectedRunId, note)
      await get().refreshDetail()
      get().startPolling()
    } finally {
      set({ approving: false })
    }
  },
}))
