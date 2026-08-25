import { useRunStore } from '../../store/useRunStore'

const TABS = [
  ['overview', 'Overview'],
  ['runs', 'Runs'],
  ['mapping', 'Mapping'],
]

export default function NavTabs() {
  const currentPage = useRunStore((s) => s.currentPage)
  const selectedRunId = useRunStore((s) => s.selectedRunId)
  const goToPage = useRunStore((s) => s.goToPage)

  return (
    <nav className="border-b border-line bg-surface">
      <div className="mx-auto flex h-10 max-w-[1600px] items-center gap-1 px-8">
        {TABS.map(([key, label]) => {
          const active = !selectedRunId && currentPage === key
          return (
            <button
              key={key}
              onClick={() => goToPage(key)}
              className={`rounded px-3 py-1.5 text-xs font-medium transition-colors ${
                active ? 'bg-surface-raised text-ink' : 'text-ink-muted hover:text-ink'
              }`}
            >
              {label}
            </button>
          )
        })}
      </div>
    </nav>
  )
}
