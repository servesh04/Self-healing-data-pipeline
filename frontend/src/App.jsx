import { useRunStore } from './store/useRunStore'
import RunList from './components/RunList/RunList'
import RunDetail from './pages/RunDetail'

export default function App() {
  const selectedRunId = useRunStore((s) => s.selectedRunId)

  return (
    <main className="min-h-screen bg-neutral-950 px-4 py-6 font-mono text-neutral-200 sm:px-8">
      <div className="mx-auto max-w-5xl">
        <header className="mb-6 border-b border-neutral-800 pb-4">
          <h1 className="text-lg font-semibold text-neutral-50">Self-Healing Data Pipeline</h1>
          <p className="mt-1 text-xs text-neutral-500">
            LangGraph healing agent — trigger a run, watch it diagnose and heal (or ask a human).
          </p>
        </header>

        {selectedRunId ? <RunDetail /> : <RunList />}
      </div>
    </main>
  )
}
