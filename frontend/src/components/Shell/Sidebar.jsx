import { NavLink, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../../store/useAuthStore'
import { useRunStore } from '../../store/useRunStore'
import {
  IconClose,
  IconContracts,
  IconIngest,
  IconLogout,
  IconMapping,
  IconOverview,
  IconRuns,
  IconWorkspace,
} from './icons'

const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: IconOverview, end: true },
  { to: '/ingestion', label: 'Data Ingestion', icon: IconIngest },
  { to: '/contracts', label: 'Data Contracts', icon: IconContracts },
  { to: '/runs', label: 'Pipeline Runs', icon: IconRuns },
  { to: '/mapping', label: 'Mapping & Approvals', icon: IconMapping },
]

function initials(name) {
  const parts = String(name ?? '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
}

function navItemClass({ isActive }) {
  return [
    'group relative flex items-center gap-3 rounded-md px-3 py-2 text-[13px] transition-colors',
    isActive
      ? 'bg-surface-raised font-medium text-ink'
      : 'text-ink-muted hover:bg-surface-raised/70 hover:text-ink',
  ].join(' ')
}

export default function Sidebar({ open, onClose }) {
  const user = useAuthStore((s) => s.session?.user)
  const signOut = useAuthStore((s) => s.signOut)
  const navigate = useNavigate()

  function handleSignOut() {
    useRunStore.getState().clearSelection()
    signOut()
    onClose?.()
    navigate('/login', { replace: true })
  }

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="fixed inset-0 z-40 bg-black/60 lg:hidden"
        />
      )}

      <aside
        aria-label="Primary navigation"
        className={`fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-line bg-surface transition-transform duration-200 ease-out lg:translate-x-0 ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="relative flex h-14 items-center gap-2.5 border-b border-line px-4">
          <span
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-signal-dim bg-signal/10 text-base text-signal"
            aria-hidden="true"
          >
            ↺
          </span>
          <div className="min-w-0 flex-1 leading-tight">
            <p className="truncate font-display text-[15px] font-semibold tracking-tight text-ink">
              Self-Healing Pipeline
            </p>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
              Operations console
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="absolute right-2 top-3 rounded p-1.5 text-ink-muted hover:bg-surface-raised hover:text-ink lg:hidden"
            aria-label="Close navigation"
          >
            <IconClose />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto px-2.5 py-4">
          <p className="px-3 pb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
            Workspace
          </p>
          <ul className="space-y-0.5">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  onClick={onClose}
                  className={navItemClass}
                >
                  {({ isActive }) => (
                    <>
                      <span
                        className={`absolute left-0 h-5 w-0.5 rounded-r ${
                          isActive ? 'bg-signal' : 'bg-transparent'
                        }`}
                        aria-hidden="true"
                      />
                      <item.icon
                        className={`h-4 w-4 shrink-0 ${
                          isActive ? 'text-signal' : 'text-ink-faint group-hover:text-ink-muted'
                        }`}
                      />
                      <span className="truncate">{item.label}</span>
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>

          <div className="my-4 border-t border-line" />

          <ul>
            <li>
              <NavLink to="/workspace" onClick={onClose} className={navItemClass}>
                {({ isActive }) => (
                  <>
                    <span
                      className={`absolute left-0 h-5 w-0.5 rounded-r ${
                        isActive ? 'bg-signal' : 'bg-transparent'
                      }`}
                      aria-hidden="true"
                    />
                    <IconWorkspace
                      className={`h-4 w-4 shrink-0 ${
                        isActive ? 'text-signal' : 'text-ink-faint group-hover:text-ink-muted'
                      }`}
                    />
                    <span className="truncate">My Workspace</span>
                  </>
                )}
              </NavLink>
            </li>
          </ul>
        </nav>

        <div className="border-t border-line p-3">
          <NavLink
            to="/workspace"
            onClick={onClose}
            className="flex items-center gap-2.5 rounded-md px-1.5 py-1.5 hover:bg-surface-raised"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-signal-dim bg-signal/15 font-mono text-[11px] font-semibold text-signal">
              {initials(user?.name)}
            </span>
            <span className="min-w-0 flex-1 leading-tight">
              <span className="block truncate text-[13px] font-medium text-ink">
                {user?.name ?? 'Demo user'}
              </span>
              <span className="block truncate text-[11px] text-ink-faint">
                {user?.workspace ?? 'Demo workspace'}
              </span>
            </span>
          </NavLink>

          <div className="mt-2 flex items-center justify-between gap-2 px-1.5">
            <span
              className="rounded border border-signal-dim/70 px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] text-signal"
              title="Credentials are checked in the browser only — this is not production security."
            >
              Prototype auth
            </span>
            <button
              type="button"
              onClick={handleSignOut}
              className="flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] text-ink-muted hover:bg-surface-raised hover:text-ink"
            >
              <IconLogout className="h-3.5 w-3.5" />
              Sign out
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}
