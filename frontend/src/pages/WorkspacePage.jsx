import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getPing } from '../api/client'
import { SESSION_STORAGE_KEY, maskToken } from '../lib/session'
import { isDemoTokenConfigured, useAuthStore } from '../store/useAuthStore'
import { useRunStore } from '../store/useRunStore'
import { IconArrowRight, IconLogout, IconShield } from '../components/Shell/icons'

function initials(name) {
  const parts = String(name ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
}

function DetailRow({ label, children, mono = false }) {
  return (
    <div className="flex flex-col gap-0.5 border-b border-line py-2.5 last:border-b-0 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6">
      <dt className="font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint">{label}</dt>
      <dd className={`min-w-0 break-words text-[13px] text-ink ${mono ? 'font-mono' : ''}`}>
        {children}
      </dd>
    </div>
  )
}

function Card({ title, description, children, className = '' }) {
  return (
    <section className={`rounded-lg border border-line bg-surface p-4 sm:p-5 ${className}`}>
      <h2 className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
        {title}
      </h2>
      {description && <p className="mt-1.5 text-xs leading-relaxed text-ink-muted">{description}</p>}
      <div className="mt-3">{children}</div>
    </section>
  )
}

function StatusDot({ tone }) {
  const color = tone === 'ok' ? 'bg-pass' : tone === 'error' ? 'bg-fail' : 'bg-running'
  return <span className={`inline-block h-1.5 w-1.5 rounded-full ${color}`} aria-hidden="true" />
}

export default function WorkspacePage() {
  const session = useAuthStore((s) => s.session)
  const updateDisplayName = useAuthStore((s) => s.updateDisplayName)
  const signOut = useAuthStore((s) => s.signOut)
  const navigate = useNavigate()

  const user = session?.user
  const [nameDraft, setNameDraft] = useState(user?.name ?? '')
  const [saved, setSaved] = useState(false)
  const [ping, setPing] = useState({ state: 'loading', data: null, error: null })

  useEffect(() => {
    let cancelled = false
    getPing()
      .then((data) => {
        if (!cancelled) setPing({ state: 'ready', data, error: null })
      })
      .catch((error) => {
        if (!cancelled) setPing({ state: 'error', data: null, error: error.message })
      })
    return () => {
      cancelled = true
    }
  }, [])

  function handleSaveName(event) {
    event.preventDefault()
    if (updateDisplayName(nameDraft)) setSaved(true)
  }

  function handleSignOut() {
    useRunStore.getState().clearSelection()
    signOut()
    navigate('/login', { replace: true })
  }

  if (!user) return null

  const apiTone = ping.state === 'ready' && ping.data?.ok ? 'ok' : ping.state === 'error' ? 'error' : 'pending'
  const apiLabel =
    ping.state === 'loading' ? 'Checking…' : ping.state === 'error' ? 'Unreachable' : ping.data?.ok ? 'Connected' : 'Degraded'
  const database = ping.data?.database

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-signal">
            Account
          </p>
          <h1 className="mt-1.5 font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
            My workspace
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-ink-muted">
            Profile, prototype session and the live environment this console is connected to.
          </p>
        </div>
        <button
          type="button"
          onClick={handleSignOut}
          className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-surface px-3 py-2 text-xs text-ink-muted hover:border-fail/60 hover:text-fail"
        >
          <IconLogout className="h-3.5 w-3.5" />
          Sign out
        </button>
      </header>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
        <Card title="Profile" description="Details attached to the active demo session.">
          <div className="flex items-center gap-3.5 border-b border-line pb-4">
            <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-signal-dim bg-signal/15 font-mono text-sm font-semibold text-signal">
              {initials(user.name)}
            </span>
            <div className="min-w-0">
              <p className="truncate font-display text-lg font-semibold tracking-tight text-ink">
                {user.name}
              </p>
              <p className="truncate text-xs text-ink-muted">{user.role}</p>
            </div>
            <span className="ml-auto shrink-0 rounded border border-line-strong px-2 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-ink-faint">
              Demo account
            </span>
          </div>

          <dl className="mt-1">
            <DetailRow label="Email" mono>
              {user.email}
            </DetailRow>
            <DetailRow label="Role">{user.role}</DetailRow>
            <DetailRow label="Workspace">{user.workspace}</DetailRow>
            <DetailRow label="User ID" mono>
              {user.id}
            </DetailRow>
          </dl>
        </Card>

        <div className="space-y-4">
          <Card
            title="Display name"
            description="Used in the sidebar and on approvals shown in this browser."
          >
            <form onSubmit={handleSaveName} className="space-y-2.5">
              <label htmlFor="display-name" className="sr-only">
                Display name
              </label>
              <input
                id="display-name"
                value={nameDraft}
                onChange={(event) => {
                  setNameDraft(event.target.value)
                  setSaved(false)
                }}
                maxLength={80}
                className="w-full rounded-md border border-line-strong bg-surface-raised px-3 py-2 font-mono text-sm text-ink focus:border-signal-dim focus:outline-none focus:ring-2 focus:ring-signal/20"
              />
              <div className="flex items-center gap-3">
                <button
                  type="submit"
                  disabled={!nameDraft.trim() || nameDraft.trim() === user.name}
                  className="rounded-md border border-line-strong bg-surface-raised px-3 py-1.5 text-xs font-medium text-ink hover:border-signal-dim disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Save name
                </button>
                {saved && (
                  <span className="text-[11px] text-pass" role="status">
                    Saved in this browser.
                  </span>
                )}
              </div>
              <p className="text-[10px] leading-relaxed text-ink-faint">
                Local only — the prototype does not send profile changes to the API.
              </p>
            </form>
          </Card>

          <Card title="Quick links">
            <div className="space-y-1">
              <Link
                to="/runs"
                className="flex items-center justify-between rounded px-1.5 py-1.5 text-xs text-ink-muted hover:bg-surface-raised hover:text-ink"
              >
                Pipeline runs
                <IconArrowRight className="h-3.5 w-3.5" />
              </Link>
              <Link
                to="/contracts"
                className="flex items-center justify-between rounded px-1.5 py-1.5 text-xs text-ink-muted hover:bg-surface-raised hover:text-ink"
              >
                Data contracts
                <IconArrowRight className="h-3.5 w-3.5" />
              </Link>
              <Link
                to="/mapping"
                className="flex items-center justify-between rounded px-1.5 py-1.5 text-xs text-ink-muted hover:bg-surface-raised hover:text-ink"
              >
                Mapping &amp; approvals
                <IconArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </Card>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Prototype session">
          <dl>
            <DetailRow label="Authentication">
              <span className="inline-flex items-center gap-1.5">
                <StatusDot tone="pending" />
                Demo credentials · browser-only
              </span>
            </DetailRow>
            <DetailRow label="API token">
              {isDemoTokenConfigured ? 'Shared demo token (VITE_SHARED_TOKEN)' : 'Not configured'}
            </DetailRow>
            <DetailRow label="Token value" mono>
              {maskToken(session?.token)}
            </DetailRow>
            <DetailRow label="Storage" mono>
              localStorage · {SESSION_STORAGE_KEY}
            </DetailRow>
          </dl>

          <div className="mt-3 flex gap-2.5 rounded border border-signal-dim/60 bg-signal/5 px-3 py-2.5">
            <IconShield className="mt-0.5 h-4 w-4 shrink-0 text-signal" />
            <p className="text-[11px] leading-relaxed text-ink-muted">
              Prototype authentication only. The UI is gated in the browser; the API still accepts
              the bundled shared token, so this is not a production security boundary.
            </p>
          </div>
        </Card>

        <Card
          title="Environment"
          description="Authenticated round-trip to the configured backend."
        >
          <dl>
            <DetailRow label="API">
              <span className="inline-flex items-center gap-1.5">
                <StatusDot tone={apiTone} />
                {apiLabel}
              </span>
            </DetailRow>
            <DetailRow label="Application env" mono>
              {ping.data?.env ?? '—'}
            </DetailRow>
            <DetailRow label="Pipeline env" mono>
              {ping.data?.pipeline_env ?? '—'}
            </DetailRow>
            <DetailRow label="Database">
              {ping.state === 'loading'
                ? 'Checking…'
                : database?.connected
                  ? `Connected${database.server_version ? ` · ${database.server_version}` : ''}`
                  : (database?.error ?? 'Unavailable')}
            </DetailRow>
          </dl>

          {ping.state === 'error' && (
            <p className="mt-3 rounded border border-fail/50 bg-fail/10 px-3 py-2 text-[11px] leading-relaxed text-fail">
              {ping.error}
            </p>
          )}

          {ping.data?.versions && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {Object.entries(ping.data.versions)
                .filter(([, version]) => version)
                .slice(0, 4)
                .map(([name, version]) => (
                  <span
                    key={name}
                    className="rounded border border-line px-1.5 py-0.5 font-mono text-[9px] text-ink-faint"
                  >
                    {name} {version}
                  </span>
                ))}
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
