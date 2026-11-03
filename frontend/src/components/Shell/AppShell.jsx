import { useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { SESSION_STORAGE_KEY } from '../../lib/session'
import { useAuthStore } from '../../store/useAuthStore'
import NewRunModal from './NewRunModal'
import RunRouteSync from './RunRouteSync'
import Sidebar from './Sidebar'
import TopBar from './TopBar'

/**
 * Protected application frame: persistent sidebar, sticky top bar, routed
 * content. The NewRunModal stays mounted here because it is app-global.
 */
export default function AppShell() {
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()
  const syncFromStorage = useAuthStore((s) => s.syncFromStorage)

  // Close the mobile drawer whenever the route changes.
  useEffect(() => {
    setNavOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!navOpen) return undefined
    const onKeyDown = (event) => {
      if (event.key === 'Escape') setNavOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [navOpen])

  // Keep two tabs of the prototype session in sync.
  useEffect(() => {
    const onStorage = (event) => {
      if (event.key === SESSION_STORAGE_KEY) syncFromStorage()
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [syncFromStorage])

  return (
    <div className="min-h-screen bg-canvas font-mono text-ink">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded focus:border focus:border-line-strong focus:bg-surface focus:px-3 focus:py-2 focus:text-xs focus:text-ink"
      >
        Skip to content
      </a>

      <RunRouteSync />
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />

      <div className="lg:pl-64">
        <TopBar onOpenNav={() => setNavOpen(true)} />
        <main
          id="main-content"
          className="mx-auto w-full max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8"
        >
          <Outlet />
        </main>
      </div>

      <NewRunModal />
    </div>
  )
}
