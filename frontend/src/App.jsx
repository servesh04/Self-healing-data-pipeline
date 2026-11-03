import { useEffect } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { SESSION_EXPIRED_EVENT } from './lib/session'
import { useAuthStore } from './store/useAuthStore'
import { useRunStore } from './store/useRunStore'
import AppShell from './components/Shell/AppShell'
import RequireAuth from './components/Shell/RequireAuth'
import ContractsPage from './pages/ContractsPage'
import IngestPage from './pages/IngestPage'
import Overview from './pages/Overview'
import RunsPage from './pages/RunsPage'
import RunDetail from './pages/RunDetail'
import MappingPage from './pages/MappingPage'
import LoginPage from './pages/LoginPage'
import WorkspacePage from './pages/WorkspacePage'

/**
 * When the API rejects the session token, the client clears the prototype
 * session and emits this event. Return to the login screen once, rather than
 * leaving a protected page in a permanent error state.
 */
function SessionExpiryWatcher() {
  const navigate = useNavigate()

  useEffect(() => {
    const onExpired = () => {
      useRunStore.getState().clearSelection()
      useAuthStore.getState().signOut()
      navigate('/login', { replace: true })
    }
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired)
  }, [navigate])

  return null
}

/**
 * Integrated Route table.
 */
export default function App() {
  return (
    <>
      <SessionExpiryWatcher />
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        <Route element={<RequireAuth />}>
          <Route element={<AppShell />}>
            <Route index element={<Overview />} />
            <Route path="ingestion" element={<IngestPage />} />
            <Route path="contracts" element={<ContractsPage />} />
            <Route path="runs" element={<RunsPage />} />
            <Route path="runs/:runId" element={<RunDetail />} />
            <Route path="mapping" element={<MappingPage />} />
            <Route path="workspace" element={<WorkspacePage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Routes>
    </>
  )
}
