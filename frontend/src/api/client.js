/**
 * Minimal API client.
 *
 * Every /api/* route requires `Authorization: Bearer <token>`. The token comes
 * from VITE_SHARED_TOKEN, which Vite inlines at build time.
 */

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
const TOKEN = import.meta.env.VITE_SHARED_TOKEN ?? ''

export const apiBase = BASE

async function request(path, { auth = true, ...opts } = {}) {
  const headers = { Accept: 'application/json', ...(opts.headers ?? {}) }
  if (auth) headers.Authorization = `Bearer ${TOKEN}`

  const res = await fetch(`${BASE}${path}`, { ...opts, headers })
  const text = await res.text()

  let body
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = { raw: text }
  }

  if (!res.ok) {
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
export const listRuns = ({ limit, status, dataset } = {}) => {
  const params = new URLSearchParams()
  if (limit) params.set('limit', limit)
  if (status) params.set('status', status)
  if (dataset) params.set('dataset', dataset)
  const qs = params.toString()
  return request(`/api/runs${qs ? `?${qs}` : ''}`)
}
export const triggerRun = (sourcePath) =>
  request('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_path: sourcePath }),
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
export const getCurrentMapping = () => request('/api/config/mapping')
export const getMappingHistory = (limit = 50) => request(`/api/config/mapping/history?limit=${limit}`)

// ── DASHBOARD.md: read-only analytics ───────────────────────────────────
export const getAnalyticsSummary = () => request('/api/analytics/summary')
export const getAnalyticsConfidence = () => request('/api/analytics/confidence')
export const getAnalyticsDriftDistribution = () => request('/api/analytics/drift_distribution')
export const getAnalyticsSpecialistPerformance = () => request('/api/analytics/specialist_performance')
