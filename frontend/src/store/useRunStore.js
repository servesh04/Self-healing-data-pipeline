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
  runs: [],
  runsLoading: false,
  runsError: null,

  selectedRunId: null,
  detail: null,
  detailError: null,

  triggering: false,
  triggerError: null,
  approving: false,

  async fetchRuns() {
    set({ runsLoading: true, runsError: null })
    try {
      const { runs } = await api.listRuns()
      set({ runs, runsLoading: false })
    } catch (err) {
      set({ runsError: err.message, runsLoading: false })
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
      const { run_id } = await api.triggerRun(sourcePath)
      await get().fetchRuns()
      set({ triggering: false })
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
