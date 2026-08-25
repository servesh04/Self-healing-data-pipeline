import { useEffect } from 'react'
import { useRunStore } from '../store/useRunStore'
import RunsTable from '../components/RunsTable/RunsTable'

export default function RunsPage() {
  const runs = useRunStore((s) => s.runs)
  const runsLoading = useRunStore((s) => s.runsLoading)
  const runsError = useRunStore((s) => s.runsError)
  const runsFilter = useRunStore((s) => s.runsFilter)
  const fetchRuns = useRunStore((s) => s.fetchRuns)

  useEffect(() => {
    fetchRuns()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  if (runsError) return <p className="text-xs text-fail">{runsError}</p>

  return (
    <RunsTable
      runs={runs}
      loading={runsLoading}
      showFilters
      filter={runsFilter.status}
      onFilterChange={(status) => fetchRuns({ ...runsFilter, status })}
    />
  )
}
