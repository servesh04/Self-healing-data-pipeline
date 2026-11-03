import { create } from 'zustand'
import {
  DEMO_ACCOUNT,
  DEMO_USER,
  clearSession,
  readSession,
  writeSession,
} from '../lib/session'

const DEMO_TOKEN = import.meta.env.VITE_SHARED_TOKEN ?? ''

/**
 * Prototype authentication store.
 *
 * There is no backend auth endpoint. Credentials are matched in the browser
 * against a single clearly-labelled demo account, and the resulting "token" is
 * the existing VITE_SHARED_TOKEN so the rest of the app keeps using the real
 * API authentication path. This is a UI demo, not a security boundary.
 */
export const useAuthStore = create((set, get) => ({
  session: readSession(),
  signInError: null,

  signIn({ email, password } = {}) {
    const normalizedEmail = String(email ?? '').trim().toLowerCase()
    const matches =
      normalizedEmail === DEMO_ACCOUNT.email && password === DEMO_ACCOUNT.password

    if (!matches) {
      set({ signInError: 'Those credentials do not match the demo account.' })
      return false
    }

    const session = { token: DEMO_TOKEN, user: { ...DEMO_USER } }
    writeSession(session)
    set({ session, signInError: null })
    return true
  },

  signOut() {
    clearSession()
    set({ session: null, signInError: null })
  },

  clearSignInError() {
    set({ signInError: null })
  },

  /** Local-only profile edit; the pinned session shape is preserved. */
  updateDisplayName(name) {
    const session = get().session
    const trimmed = String(name ?? '').trim()
    if (!session || !trimmed) return false

    const next = { ...session, user: { ...session.user, name: trimmed } }
    writeSession(next)
    set({ session: next })
    return true
  },

  /** Re-read after a storage event (another tab signed in/out). */
  syncFromStorage() {
    set({ session: readSession() })
  },
}))

export const isDemoTokenConfigured = Boolean(DEMO_TOKEN)
