import { useRunStore } from './store/useRunStore'
import TopBar from './components/Shell/TopBar'
import NavTabs from './components/Shell/NavTabs'
import NewRunModal from './components/Shell/NewRunModal'
import Overview from './pages/Overview'
import RunsPage from './pages/RunsPage'
import MappingPage from './pages/MappingPage'
import RunDetail from './pages/RunDetail'

const PAGES = {
  overview: Overview,
  runs: RunsPage,
  mapping: MappingPage,
}

export default function App() {
  const selectedRunId = useRunStore((s) => s.selectedRunId)
  const currentPage = useRunStore((s) => s.currentPage)
  const Page = PAGES[currentPage] ?? Overview

  return (
    <div className="min-h-screen bg-canvas font-mono text-ink">
      <TopBar />
      <NavTabs />
      <div className="mx-auto max-w-[1600px] px-8 py-6">
        {selectedRunId ? <RunDetail /> : <Page />}
      </div>
      <NewRunModal />
    </div>
  )
}
