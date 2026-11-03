/**
 * Data Contracts API.
 *
 * Self-contained on purpose: the shared `api/client.js` only knows
 * VITE_SHARED_TOKEN, while the app shell stores the demo session under
 * `shdp.session`. Per the orchestration contract this module reads the
 * session token first and falls back to the build-time token, without
 * changing the shared client.
 *
 * The backend parses YAML/JSON by content, never writes an upload to disk,
 * and returns `{detail}` on error. Errors here carry `.status` and `.detail`
 * so the page can distinguish 409/413/422.
 */

const BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
export const MAX_CONTRACT_BYTES = 256 * 1024

const SESSION_KEY = 'shdp.session'

export function readSessionToken() {
  if (typeof window === 'undefined' || !window.localStorage) return null
  try {
    const raw = window.localStorage.getItem(SESSION_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return typeof parsed?.token === 'string' && parsed.token ? parsed.token : null
  } catch {
    return null
  }
}

function authToken() {
  return readSessionToken() ?? import.meta.env.VITE_SHARED_TOKEN ?? ''
}

async function request(path, { auth = true, ...opts } = {}) {
  const headers = { Accept: 'application/json', ...(opts.headers ?? {}) }
  if (auth) {
    const token = authToken()
    if (token) headers.Authorization = `Bearer ${token}`
  }

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
    err.detail = body?.detail ?? null
    err.body = body
    throw err
  }
  return body
}

export const listContracts = () => request('/api/app/contracts')

export const getContract = (name) =>
  request(`/api/app/contracts/${encodeURIComponent(name)}`)

export const deleteContract = (name) =>
  request(`/api/app/contracts/${encodeURIComponent(name)}`, { method: 'DELETE' })

function uploadBody(file, name) {
  const body = new FormData()
  body.append('file', file)
  if (name) body.append('name', name)
  return body
}

/** Validate + preview without registering. `name` is optional. */
export const previewContract = (file, name) =>
  request('/api/app/contracts/preview', { method: 'POST', body: uploadBody(file, name) })

export const registerContract = (file, name) =>
  request('/api/app/contracts', { method: 'POST', body: uploadBody(file, name) })
