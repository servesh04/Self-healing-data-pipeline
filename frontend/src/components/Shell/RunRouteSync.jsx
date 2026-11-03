import { useEffect, useRef } from 'react'
import { useLocation, useMatch, useNavigate } from 'react-router-dom'
import { useRunStore } from '../../store/useRunStore'

const PAGE_BY_PATH = {
  '/': 'overview',
  '/runs': 'runs',
  '/mapping': 'mapping',
  '/ingestion': 'ingestion',
  '/contracts': 'contracts',
  '/workspace': 'workspace',
}

const RUN_DETAIL_FALLBACK = {
  overview: '/',
  runs: '/runs',
  mapping: '/mapping',
}

/**
 * Bridges the URL and the existing Zustand run selection.
 *
 * The run store predates routing: rows call `selectRun(id)`, run detail reads
 * `selectedRunId`, and its back link calls `clearSelection()`. This component
 * keeps that behaviour intact while making the selection a real, refreshable
 * `/runs/:runId` URL. Store polling/fetching is untouched.
 */
export default function RunRouteSync() {
  const location = useLocation()
  const runMatch = useMatch('/runs/:runId')
  const navigate = useNavigate()
  const routeRunId = runMatch?.params?.runId ?? null
  const selectedRunId = useRunStore((s) => s.selectedRunId)
  const lastPathRef = useRef(null)

  // URL → store. Runs only when the pathname actually changes, so clearing a
  // selection inside run detail cannot immediately re-select the same run.
  useEffect(() => {
    if (lastPathRef.current === location.pathname) return
    lastPathRef.current = location.pathname

    const state = useRunStore.getState()

    if (routeRunId) {
      if (state.selectedRunId !== routeRunId) state.selectRun(routeRunId)
      return
    }

    state.clearSelection()
    const page = PAGE_BY_PATH[location.pathname]
    if (page && state.currentPage !== page && RUN_DETAIL_FALLBACK[page]) {
      state.goToPage(page)
    }
  }, [location.pathname, routeRunId])

  // Store → URL. `selectRun` is synchronous about setting `selectedRunId`, so
  // read the fresh value rather than the one captured during this render.
  useEffect(() => {
    const freshRunId = useRunStore.getState().selectedRunId

    if (freshRunId) {
      if (routeRunId !== freshRunId) navigate(`/runs/${freshRunId}`)
      return
    }

    if (routeRunId) {
      const currentPage = useRunStore.getState().currentPage
      const target = RUN_DETAIL_FALLBACK[currentPage] ?? '/'
      navigate(target, { replace: true })
    }
  }, [selectedRunId, routeRunId, navigate])

  return null
}
