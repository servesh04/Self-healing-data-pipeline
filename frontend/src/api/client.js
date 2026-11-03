/**
 * Minimal API client.
 *
 * Every /api/* route requires `Authorization: Bearer <token>`. The token is
 * read from the prototype session (`shdp.session`) first and falls back to
 * VITE_SHARED_TOKEN, which Vite inlines at build time. The demo session stores
 * that same shared token, so swapping the source does not change the API
 * contract.
 */

import {
  SESSION_EXPIRED_EVENT,
  clearSession,
  readSession,
} from '../lib/session'

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
const TOKEN = import.meta.env.VITE_SHARED_TOKEN ?? ''

export const apiBase = BASE

function resolveToken() {
  return readSession()?.token || TOKEN
}

export async function request(path, { auth = true, ...opts } = {}) {
  const headers = { Accept: 'application/json', ...(opts.headers ?? {}) }
  const token = auth ? resolveToken() : ''
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${BASE}${path}`, { ...opts, headers })
  const text = await res.text()

  let body
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = { raw: text }
  }

  if (!res.ok) {
    // A 401 against a signed-in session means the shared token is no longer
    // accepted. Drop the prototype session and let the shell return to /login
    // rather than leaving the user on a page that can never load.
    if (res.status === 401 && auth && readSession()) {
      clearSession()
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    }

    const err = new Error(body?.detail ?? `HTTP ${res.status}`)
    err.status = res.status
    err.body = body
    throw err
  }
  return body
}

export const getHealth = () => request('/health', { auth: false })
export const getPing = () => request('/api/ping')

/** Deliberately unauthenticated call to /api/ping — must be rejected with 401. */
export async function probeAuthRejection() {
  try {
    await request('/api/ping', { auth: false })
    return { rejected: false, status: 200 }
  } catch (err) {
    return { rejected: err.status === 401, status: err.status ?? 0 }
  }
}

// ── Phase 5: runs + config ───────────────────────────────────────────────
export const listRuns = ({ limit, status, dataset, contractName } = {}) => {
  const params = new URLSearchParams()
  if (limit) params.set('limit', limit)
  if (status) params.set('status', status)
  if (dataset) params.set('dataset', dataset)
  if (contractName && contractName !== 'all') params.set('contract_name', contractName)
  const qs = params.toString()
  return request(`/api/runs${qs ? `?${qs}` : ''}`)
}
export const triggerRun = (sourceId, contractName = 'default') =>
  request('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId, contract_name: contractName }),
  })
export const getRun = (runId) => request(`/api/runs/${runId}`)
export const approveRun = (runId, note) =>
  request(`/api/runs/${runId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note: note ?? null }),
  })
export const rejectRun = (runId, note) =>
  request(`/api/runs/${runId}/reject`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ note: note ?? null }),
  })
export const getContracts = () => request('/api/config/contracts')
export const getSources = (contractName = 'default') => request(`/api/config/sources?contract_name=${encodeURIComponent(contractName)}`)
export const getCurrentMapping = (contractName = 'default') => request(`/api/config/mapping?contract_name=${encodeURIComponent(contractName)}`)
export const getMappingHistory = (limit = 50, contractName = 'default') => request(`/api/config/mapping/history?limit=${limit}&contract_name=${encodeURIComponent(contractName)}`)

// ── DASHBOARD.md: read-only analytics ───────────────────────────────────
export const getAnalyticsSummary = (contractName = 'default') => request(`/api/analytics/summary?contract_name=${encodeURIComponent(contractName)}`)
export const getAnalyticsConfidence = (contractName = 'default') => request(`/api/analytics/confidence?contract_name=${encodeURIComponent(contractName)}`)
export const getAnalyticsDriftDistribution = (contractName = 'default') => request(`/api/analytics/drift_distribution?contract_name=${encodeURIComponent(contractName)}`)
export const getAnalyticsSpecialistPerformance = (contractName = 'default') => request(`/api/analytics/specialist_performance?contract_name=${encodeURIComponent(contractName)}`)
export const getComponentMetrics = (contractName = 'default') => request(`/api/analytics/components?contract_name=${encodeURIComponent(contractName)}`)

// ── Read-only chat over run data ─────────────────────────────────────────
// messages: [{role: 'user'|'assistant', content: string}]. Read-only —
// there is no equivalent trigger/approve/reject call this can make.
export const postRunChat = (runId, messages) =>
  request(`/api/chat/${runId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  })

// Global scope's context is ~5x a single run's (aggregates + every run's
// lean record), which occasionally pushes a request past Groq's per-minute
// budget when called back-to-back — found while verifying this against
// real seeded data: the backend's LLM call has a hardcoded 30s timeout
// (services/llm.py, outside this feature's file boundary to change) and
// can genuinely exceed it under that transient pressure, surfacing as a
// 503. One quiet retry here is cheap insurance against that specific,
// observed failure mode — not a generic "retry everything" policy.
export const postGlobalChat = async (messages, contractName = 'default') => {
  try {
    return await request(`/api/chat?contract_name=${encodeURIComponent(contractName)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'global', messages }),
    })
  } catch (err) {
    if (err.status !== 503) throw err
    return request(`/api/chat?contract_name=${encodeURIComponent(contractName)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'global', messages }),
    })
  }
}
