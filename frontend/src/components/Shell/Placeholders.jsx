import { Link } from 'react-router-dom'
import { IconArrowRight } from './icons'

/**
 * Temporary route targets for pages owned by the contracts and ingestion
 * agents. Integration replaces the two alias imports in App.jsx with the real
 * page modules; the route entries themselves stay unchanged.
 */
function PlaceholderPage({ eyebrow, title, description, capabilities, note }) {
  return (
    <div className="mx-auto max-w-3xl space-y-6 py-4">
      <header>
        <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-signal">
          {eyebrow}
        </p>
        <h1 className="mt-1.5 font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
          {title}
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-ink-muted">{description}</p>
      </header>

      <section className="rounded-lg border border-line bg-surface p-5">
        <h2 className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
          Planned in this area
        </h2>
        <ul className="mt-3 space-y-2.5">
          {capabilities.map((item) => (
            <li key={item} className="flex items-start gap-2.5 text-sm text-ink-muted">
              <span
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-signal-dim"
                aria-hidden="true"
              />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      <div className="rounded-lg border border-line-strong border-l-2 border-l-signal bg-surface/60 px-4 py-3">
        <p className="text-xs leading-relaxed text-ink-muted">{note}</p>
      </div>

      <div className="flex flex-wrap gap-2">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 rounded-md border border-line-strong bg-surface-raised px-3 py-2 text-xs font-medium text-ink hover:border-signal-dim"
        >
          Back to Overview
        </Link>
        <Link
          to="/runs"
          className="inline-flex items-center gap-1.5 rounded-md px-3 py-2 text-xs text-ink-muted hover:text-ink"
        >
          View pipeline runs
          <IconArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>
    </div>
  )
}

export function IngestPlaceholder() {
  return (
    <PlaceholderPage
      eyebrow="Data"
      title="Data Ingestion"
      description="Upload a CSV, inspect the inferred schema and preview rows before sending the dataset through the healing pipeline."
      capabilities={[
        'Bounded CSV upload with server-side size and row limits',
        'Column type inference and a first-rows preview',
        'Dataset metadata, status and deletion',
        'Trigger a pipeline run directly from an uploaded dataset',
      ]}
      note="This route is a placeholder. The URL, authentication guard, sidebar entry and layout are already wired; the ingestion page component is delivered separately and will be swapped in at integration."
    />
  )
}

export function ContractsPlaceholder() {
  return (
    <PlaceholderPage
      eyebrow="Data"
      title="Data Contracts"
      description="Manage the schema, type and quality expectations every dataset is validated against."
      capabilities={[
        'List file-based and uploaded contracts',
        'Upload a YAML contract with validation feedback',
        'Inspect columns, checks and contract versions',
        'Delete uploaded contracts while protecting immutable built-ins',
      ]}
      note="This route is a placeholder. The URL, authentication guard, sidebar entry and layout are already wired; the contracts page component is delivered separately and will be swapped in at integration."
    />
  )
}
