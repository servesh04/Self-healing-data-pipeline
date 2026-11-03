import { useEffect, useState } from 'react'
import { NavLink, useLocation, useMatch, useNavigate } from 'react-router-dom'
import { getPing } from '../../api/client'
import { useRunStore } from '../../store/useRunStore'
import { useAuthStore } from '../../store/useAuthStore'
import { IconLogout, IconMenu, IconPlus } from './icons'

const PAGE_META = {
  '/': ['Overview', 'System behaviour at rest'],
  '/ingestion': ['Data Ingestion', 'Upload, preview and prepare source data'],
  '/contracts': ['Data Contracts', 'Schema and quality expectations'],
  '/runs': ['Pipeline Runs', 'Every execution, newest first'],
  '/mapping': ['Mapping & Approvals', 'Applied patches and human decisions'],
  '/workspace': ['My Workspace', 'Profile, session and environment'],
}

function initials(name) {
  const parts = String(name ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
}

export default function TopBar({ onOpenNav }) {
  const location = useLocation()
  const runMatch = useMatch('/runs/:runId')
  const navigate = useNavigate()
  const [appEnv, setAppEnv] = useState(null)
  const [envError, setEnvError] = useState(false)

  const openNewRun = useRunStore((s) => s.openNewRun)
  const contracts = useRunStore((s) => s.contracts)
  const contractName = useRunStore((s) => s.contractName)
  const fetchContracts = useRunStore((s) => s.fetchContracts)
  const fetchSources = useRunStore((s) => s.fetchSources)
  const setContractName = useRunStore((s) => s.setContractName)

  const user = useAuthStore((s) => s.session?.user)
  const signOut = useAuthStore((s) => s.signOut)

  useEffect(() => {
    getPing()
      .then((r) => setAppEnv(r.env))
      .catch(() => setEnvError(true))
    fetchContracts()
    fetchSources()
  }, [fetchContracts, fetchSources])

  const [title, subtitle] = runMatch
    ? ['Run detail', runMatch.params.runId ?? '']
    : (PAGE_META[location.pathname] ?? ['Overview', 'System behaviour at rest'])

  function handleSignOut() {
    // Stop run-detail polling before the protected shell unmounts.
    useRunStore.getState().clearSelection()
    signOut()
    navigate('/login', { replace: true })
  }

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur">
      <div className="flex h-14 items-center gap-3 px-4 sm:px-6 lg:px-8">
        <button
          type="button"
          onClick={onOpenNav}
          className="rounded-md p-1.5 text-ink-muted hover:bg-surface-raised hover:text-ink lg:hidden"
          aria-label="Open navigation"
        >
          <IconMenu className="h-5 w-5" />
        </button>

        <div className="min-w-0">
          <p className="truncate text-[13px] font-medium text-ink">{title}</p>
          <p className="hidden truncate font-mono text-[10px] uppercase tracking-[0.12em] text-ink-faint sm:block">
            {subtitle}
          </p>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {(appEnv || envError) && (
            <span
              className="hidden items-center gap-1.5 rounded border border-line-strong px-2 py-1 font-mono text-[10px] uppercase tracking-wide text-ink-faint md:inline-flex"
              title={envError ? 'Environment probe failed' : 'Application environment'}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${envError ? 'bg-fail' : 'bg-pass'}`}
                aria-hidden="true"
              />
              {appEnv ? `env: ${appEnv}` : 'api offline'}
            </span>
          )}

          <label className="hidden items-center gap-1.5 text-[11px] text-ink-muted sm:flex">
            <span className="font-mono uppercase tracking-wide text-ink-faint">Contract</span>
            <select
              aria-label="Contract"
              value={contractName}
              onChange={(e) => setContractName(e.target.value)}
              className="rounded border border-line-strong bg-surface-raised px-2 py-1 text-xs text-ink focus:border-signal-dim focus:outline-none"
            >
              {(contracts.length ? contracts : [{ name: 'default' }]).map((c) => (
                <option key={c.name} value={c.name}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>

          <button
            type="button"
            onClick={openNewRun}
            className="inline-flex items-center gap-1.5 rounded-md border border-signal bg-signal/15 px-2.5 py-1.5 text-xs font-medium text-signal hover:bg-signal/25"
          >
            <IconPlus className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">New run</span>
            <span className="sm:hidden">Run</span>
          </button>

          <span className="mx-1 hidden h-5 w-px bg-line sm:block" aria-hidden="true" />

          <NavLink
            to="/workspace"
            className="flex items-center gap-2 rounded-md px-1 py-1 hover:bg-surface-raised"
            title="My Workspace"
          >
            <span className="flex h-7 w-7 items-center justify-center rounded-full border border-signal-dim bg-signal/15 font-mono text-[10px] font-semibold text-signal">
              {initials(user?.name)}
            </span>
            <span className="hidden max-w-[9rem] truncate text-xs text-ink-muted lg:block">
              {user?.name ?? 'Demo user'}
            </span>
          </NavLink>

          <button
            type="button"
            onClick={handleSignOut}
            className="rounded-md p-1.5 text-ink-muted hover:bg-surface-raised hover:text-ink"
            aria-label="Sign out"
            title="Sign out"
          >
            <IconLogout className="h-4 w-4" />
          </button>
        </div>
      </div>
    </header>
  )
}
