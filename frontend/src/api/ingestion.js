/**
 * Ingestion API module — self-contained on purpose.
 *
 * The shared `frontend/src/api/client.js` only knows the build-time
 * VITE_SHARED_TOKEN. The app expansion adds a demo session stored in
 * localStorage under `shdp.session` (JSON: { token, user }), so this module
 * resolves the bearer token session-first with the Vite env fallback, and
 * exposes exactly the endpoints the Ingest page needs. If the integrator
 * later swaps this for the upgraded shared `request` helper, the exported
 * function names and shapes are the contract (see
 * docs/app-proposals/ingestion.md §6); nothing else in this file is
 * implementation-dependent.
 *
 * Multipart uploads must not set `Content-Type` manually — the browser
 * provides the `multipart/form-data; boundary=…` header.
 */

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
const SESSION_KEY = 'shdp.session'

export function getSessionToken() {
  try {
    const raw = window.localStorage.getItem(SESSION_KEY)
    if (raw) {
      const session = JSON.parse(raw)
      if (typeof session?.token === 'string' && session.token) return session.token
    }
  } catch {
    // Malformed or unavailable session (private mode, JSON flood): fall
    // through to the build-time token.
  }
  return import.meta.env.VITE_SHARED_TOKEN ?? ''
}

async function request(path, { method = 'GET', body, form = false } = {}) {
  const headers = { Accept: 'application/json' }
  const token = getSessionToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (body !== undefined && !form) headers['Content-Type'] = 'application/json'

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: form ? body : body === undefined ? undefined : JSON.stringify(body),
  })
  const text = await res.text()

  let data
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = { raw: text }
  }

  if (!res.ok) {
    const err = new Error(data?.detail ?? `HTTP ${res.status}`)
    err.status = res.status
    err.body = data
    throw err
  }
  return data
}

// ── Datasets: upload, list, preview, delete ────────────────────────────────
export const uploadDataset = (file) => {
  const form = new FormData()
  form.append('file', file)
  return request('/api/app/datasets', { method: 'POST', body: form, form: true })
}

export const listDatasets = (limit = 20) =>
  request(`/api/app/datasets?limit=${encodeURIComponent(limit)}`)

export const getDataset = (datasetId) =>
  request(`/api/app/datasets/${encodeURIComponent(datasetId)}`)

export const previewDataset = (datasetId, limit = 20) =>
  request(`/api/app/datasets/${encodeURIComponent(datasetId)}/preview?limit=${encodeURIComponent(limit)}`)

export const deleteDataset = (datasetId) =>
  request(`/api/app/datasets/${encodeURIComponent(datasetId)}`, { method: 'DELETE' })

// ── Contract selection + trigger (reuse the existing pipeline surface) ─────
export const getContracts = () => request('/api/config/contracts')

export const triggerRun = (sourceId, contractName = 'default') =>
  request('/api/runs', {
    method: 'POST',
    body: { source_id: sourceId, contract_name: contractName },
  })

/**
 * Navigate the (shell-owned) React Router to a path without importing
 * `react-router-dom` — frontend/package.json is integration-owned and the
 * dependency lands with feature/app-shell. pushState + a synthetic popstate
 * is exactly what the router itself listens for, so this works unchanged once
 * the app shell's <BrowserRouter> is in place; before that it just updates
 * the URL (harmless). Integration can replace this one-liner with
 * `useNavigate()` from the shell.
 */
export function navigateToPath(path) {
  try {
    window.history.pushState({ shdpIngestion: true }, '', path)
    window.dispatchEvent(new PopStateEvent('popstate'))
  } catch {
    window.location.assign(path)
  }
}

export const navigateToRun = (runId) => navigateToPath(`/runs/${encodeURIComponent(runId)}`)
