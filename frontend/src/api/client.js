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
