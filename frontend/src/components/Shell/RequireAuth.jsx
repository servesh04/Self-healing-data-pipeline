import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { useAuthStore } from '../../store/useAuthStore'

/**
 * Route guard for the authenticated application. The `from` location is
 * preserved so the login page can return the user to the page they asked for.
 */
export default function RequireAuth() {
  const session = useAuthStore((s) => s.session)
  const location = useLocation()

  if (!session) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <Outlet />
}
