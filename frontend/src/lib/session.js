/**
 * Prototype session storage.
 *
 * The demo session is a browser-only artefact: it gates the UI, not the API
 * (the API token is the existing shared bearer token). Keep this module free
 * of React/Zustand imports so both the auth store and the API client can read
 * the same record without a circular dependency.
 */

export const SESSION_STORAGE_KEY = 'shdp.session'

export const SESSION_EXPIRED_EVENT = 'shdp:session-expired'

export const DEMO_ACCOUNT = Object.freeze({
  email: 'demo@shdp.dev',
  password: 'heal-demo',
})

export const DEMO_USER = Object.freeze({
  id: 'usr_demo_001',
  name: 'Dana Okafor',
  email: 'demo@shdp.dev',
  role: 'Data Platform Engineer',
  workspace: 'Analytics Platform — Demo',
})

function storage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}

export function isValidSession(value) {
  return Boolean(
    value &&
      typeof value === 'object' &&
      typeof value.token === 'string' &&
      value.user &&
      typeof value.user === 'object' &&
      typeof value.user.id === 'string' &&
      typeof value.user.email === 'string' &&
      typeof value.user.name === 'string',
  )
}

export function readSession() {
  const store = storage()
  if (!store) return null
  try {
    const raw = store.getItem(SESSION_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return isValidSession(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function writeSession(session) {
  const store = storage()
  if (!store) return
  try {
    store.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
  } catch {
    /* private mode / quota — the in-memory session still works for this tab */
  }
}

export function clearSession() {
  const store = storage()
  if (!store) return
  try {
    store.removeItem(SESSION_STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

/** Mask a bearer token for display: last four characters only. */
export function maskToken(token) {
  if (!token) return 'not configured'
  if (token.length <= 4) return '••••'
  return `${'•'.repeat(Math.min(12, token.length - 4))}${token.slice(-4)}`
}
