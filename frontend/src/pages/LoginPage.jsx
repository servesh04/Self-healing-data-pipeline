import { useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { DEMO_ACCOUNT } from '../lib/session'
import { isDemoTokenConfigured, useAuthStore } from '../store/useAuthStore'
import { IconShield } from '../components/Shell/icons'

const HIGHLIGHTS = [
  {
    title: 'Drift detection that explains itself',
    body: 'Schema, distribution and temporal checks feed one run record.',
  },
  {
    title: 'Repairs gated by confidence',
    body: 'Low-confidence proposals pause for a human decision and keep the audit trail.',
  },
  {
    title: 'One operational workspace',
    body: 'Runs, contracts, mapping history and approvals in a single place.',
  },
]

const inputClass =
  'w-full rounded-md border border-line-strong bg-surface-raised px-3 py-2.5 font-mono text-sm text-ink placeholder:text-ink-faint focus:border-signal-dim focus:outline-none focus:ring-2 focus:ring-signal/20'

function Brand() {
  return (
    <div className="flex items-center gap-3">
      <span
        className="flex h-9 w-9 items-center justify-center rounded-md border border-signal-dim bg-signal/10 text-lg text-signal"
        aria-hidden="true"
      >
        ↺
      </span>
      <div className="leading-tight">
        <p className="font-display text-[15px] font-semibold tracking-tight text-ink">
          Self-Healing Pipeline
        </p>
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
          Operations console
        </p>
      </div>
    </div>
  )
}

export default function LoginPage() {
  const session = useAuthStore((s) => s.session)
  const signIn = useAuthStore((s) => s.signIn)
  const signInError = useAuthStore((s) => s.signInError)
  const clearSignInError = useAuthStore((s) => s.clearSignInError)
  const location = useLocation()

  const [email, setEmail] = useState(DEMO_ACCOUNT.email)
  const [password, setPassword] = useState(DEMO_ACCOUNT.password)
  const [showPassword, setShowPassword] = useState(false)

  const from = location.state?.from
    ? `${location.state.from.pathname}${location.state.from.search ?? ''}`
    : '/'

  if (session) return <Navigate to={from} replace />

  function handleSubmit(event) {
    event.preventDefault()
    signIn({ email, password })
  }

  function useDemoAccount() {
    setEmail(DEMO_ACCOUNT.email)
    setPassword(DEMO_ACCOUNT.password)
    clearSignInError()
  }

  return (
    <div className="min-h-screen bg-canvas font-mono text-ink lg:grid lg:grid-cols-[minmax(0,1.08fr)_minmax(0,1fr)]">
      {/* ── Product context (desktop) ─────────────────────────────────── */}
      <aside className="relative hidden flex-col justify-between border-r border-line bg-surface px-10 py-9 lg:flex xl:px-14 xl:py-12">
        <div className="flex items-center justify-between gap-4">
          <Brand />
          <span className="rounded border border-signal-dim/70 px-2 py-1 font-mono text-[9px] uppercase tracking-[0.12em] text-signal">
            Prototype build
          </span>
        </div>

        <div className="max-w-xl py-12">
          <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-signal">
            Documented, reviewable data repair
          </p>
          <h1 className="mt-3 font-display text-4xl font-semibold leading-[1.1] tracking-tight text-ink xl:text-[2.9rem]">
            Every pipeline run, accounted for.
          </h1>
          <p className="mt-4 max-w-lg text-sm leading-relaxed text-ink-muted">
            Diagnose drift, review the proposed mapping patch and keep an inspectable record of who
            approved what. When confidence is low, the system asks instead of guessing.
          </p>

          <ul className="mt-9 space-y-5 border-t border-line pt-8">
            {HIGHLIGHTS.map((item, index) => (
              <li key={item.title} className="flex gap-3.5">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded border border-line-strong font-mono text-[10px] text-ink-faint">
                  {String(index + 1).padStart(2, '0')}
                </span>
                <div>
                  <p className="text-[13px] font-medium text-ink">{item.title}</p>
                  <p className="mt-0.5 text-xs leading-relaxed text-ink-faint">{item.body}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex items-center justify-between border-t border-line pt-5 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint">
          <span>LangGraph · FastAPI · Postgres</span>
          <span>Demo environment</span>
        </div>
      </aside>

      {/* ── Sign-in form ──────────────────────────────────────────────── */}
      <main className="flex min-h-screen flex-col">
        <div className="flex items-center justify-between border-b border-line px-5 py-4 lg:hidden">
          <Brand />
          <span className="rounded border border-signal-dim/70 px-2 py-1 font-mono text-[9px] uppercase tracking-[0.12em] text-signal">
            Prototype
          </span>
        </div>

        <div className="flex flex-1 items-center justify-center px-5 py-10 sm:px-8">
          <div className="w-full max-w-[400px]">
            <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
              Sign in
            </p>
            <h2 className="mt-1.5 font-display text-2xl font-semibold tracking-tight text-ink">
              Open your workspace
            </h2>
            <p className="mt-1.5 text-sm text-ink-muted">
              Use the single demo account to explore the application.
            </p>

            <div
              role="note"
              className="mt-5 rounded-md border border-signal-dim/70 border-l-2 border-l-signal bg-signal/5 px-3.5 py-3"
            >
              <div className="flex items-center gap-1.5 text-[11px] font-semibold text-signal">
                <IconShield className="h-3.5 w-3.5" />
                Prototype authentication
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                Credentials are checked in this browser only. This gate demonstrates the product
                flow — it is <span className="text-ink">not production-grade security</span> and does
                not protect the API token.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="mt-5 space-y-4" noValidate>
              <div>
                <label
                  htmlFor="email"
                  className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.1em] text-ink-muted"
                >
                  Email
                </label>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="username"
                  spellCheck={false}
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value)
                    if (signInError) clearSignInError()
                  }}
                  className={inputClass}
                  placeholder="you@company.com"
                />
              </div>

              <div>
                <label
                  htmlFor="password"
                  className="mb-1.5 block text-[11px] font-medium uppercase tracking-[0.1em] text-ink-muted"
                >
                  Password
                </label>
                <div className="relative">
                  <input
                    id="password"
                    name="password"
                    type={showPassword ? 'text' : 'password'}
                    autoComplete="current-password"
                    value={password}
                    onChange={(event) => {
                      setPassword(event.target.value)
                      if (signInError) clearSignInError()
                    }}
                    className={`${inputClass} pr-16`}
                    placeholder="Demo password"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((value) => !value)}
                    className="absolute inset-y-0 right-2 my-auto h-6 rounded px-1.5 text-[10px] uppercase tracking-wide text-ink-faint hover:bg-surface hover:text-ink"
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? 'Hide' : 'Show'}
                  </button>
                </div>
              </div>

              {signInError && (
                <p
                  role="alert"
                  className="rounded border border-fail/60 bg-fail/10 px-3 py-2 text-xs text-fail"
                >
                  {signInError}
                </p>
              )}

              <button
                type="submit"
                className="w-full rounded-md border border-signal bg-signal/15 px-3 py-2.5 text-sm font-medium text-signal transition-colors hover:bg-signal/25 focus:outline-none focus:ring-2 focus:ring-signal/30"
              >
                Sign in to workspace
              </button>
            </form>

            <div className="mt-6 rounded-md border border-line bg-surface p-3.5">
              <div className="flex items-center justify-between gap-3">
                <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-faint">
                  Demo credentials
                </p>
                <button
                  type="button"
                  onClick={useDemoAccount}
                  className="text-[11px] text-signal hover:underline"
                >
                  Use demo account
                </button>
              </div>
              <dl className="mt-2.5 space-y-1.5 text-[12px]">
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-faint">Email</dt>
                  <dd className="font-mono text-ink">{DEMO_ACCOUNT.email}</dd>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <dt className="text-ink-faint">Password</dt>
                  <dd className="font-mono text-ink">{DEMO_ACCOUNT.password}</dd>
                </div>
              </dl>
            </div>

            {!isDemoTokenConfigured && (
              <p className="mt-4 rounded border border-fail/50 bg-fail/10 px-3 py-2 text-[11px] leading-relaxed text-fail">
                VITE_SHARED_TOKEN is not configured in this build. Sign-in works, but every API
                request will be rejected until it is set.
              </p>
            )}

            <p className="mt-5 text-center text-[10px] leading-relaxed text-ink-faint">
              The session is stored in this browser&apos;s local storage and can be cleared at any
              time by signing out.
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
