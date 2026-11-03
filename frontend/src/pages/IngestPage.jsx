import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import DatasetPreview from '../components/Ingest/DatasetPreview'
import * as ingestApi from '../api/ingestion'

// Mirrors services/upload_store.py caps for instant feedback; the server is
// the only enforcement point (413/422) — these only stop obvious mistakes
// before an upload is even attempted.
const MAX_BYTES = 5 * 1024 * 1024
const PREVIEW_ROWS = 15

function humanSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function ErrorBanner({ title, message, onDismiss }) {
  if (!message) return null
  return (
    <div className="rounded-lg border border-fail/40 bg-fail/10 p-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold text-fail">{title}</p>
        {onDismiss && (
          <button onClick={onDismiss} className="text-[11px] text-ink-faint hover:text-ink">
            dismiss
          </button>
        )}
      </div>
      <p className="mt-1 break-words text-xs text-fail/90">{message}</p>
    </div>
  )
}

export default function IngestPage() {
  const navigate = useNavigate()
  // ── Upload + preview state ──────────────────────────────────────────────
  const [dataset, setDataset] = useState(null)
  const [preview, setPreview] = useState({ rows: [], rowsReturned: 0, truncated: false })
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [dragging, setDragging] = useState(false)
  const fileInputRef = useRef(null)

  // ── Recent datasets ─────────────────────────────────────────────────────
  const [recent, setRecent] = useState([])
  const [recentError, setRecentError] = useState(null)

  // ── Contract selection ──────────────────────────────────────────────────
  const [contracts, setContracts] = useState([])
  const [contractsError, setContractsError] = useState(null)
  const [contractName, setContractName] = useState('default')

  // ── Trigger ─────────────────────────────────────────────────────────────
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState(null)
  const [deleting, setDeleting] = useState(null)

  const refreshRecent = useCallback(async () => {
    setRecentError(null)
    try {
      const { datasets } = await ingestApi.listDatasets(10)
      setRecent(datasets ?? [])
    } catch (err) {
      setRecentError(err.message)
    }
  }, [])

  const refreshContracts = useCallback(() => {
    ingestApi
      .getContracts()
      .then(({ contracts }) => {
        setContracts(contracts ?? [])
        if (contracts?.length && !contracts.some((c) => c.name === 'default')) {
          setContractName(contracts[0].name)
        }
      })
      .catch((err) => setContractsError(err.message))
  }, [])

  useEffect(() => {
    refreshRecent()
    refreshContracts()
    window.addEventListener('shdp:contracts-updated', refreshContracts)
    return () => window.removeEventListener('shdp:contracts-updated', refreshContracts)
  }, [refreshRecent, refreshContracts])

  const loadPreview = useCallback(async (record) => {
    setDataset(record)
    setRunError(null)
    setPreviewError(null)
    setPreviewLoading(true)
    setPreview({ rows: [], rowsReturned: 0, truncated: false })
    try {
      const detail = await ingestApi.previewDataset(record.dataset_id, PREVIEW_ROWS)
      setPreview({
        rows: detail.rows ?? [],
        rowsReturned: detail.rows_returned ?? 0,
        truncated: detail.truncated ?? false,
      })
    } catch (err) {
      setPreviewError(err.message)
    } finally {
      setPreviewLoading(false)
    }
  }, [])

  const acceptFile = useCallback(
    async (file) => {
      setUploadError(null)
      setRunError(null)
      if (!file) return
      if (!/\.csv$/i.test(file.name)) {
        setUploadError(`"${file.name}" is not a .csv file — only CSV uploads are accepted.`)
        return
      }
      if (file.size > MAX_BYTES) {
        setUploadError(`"${file.name}" is ${humanSize(file.size)}; uploads are capped at 5 MB.`)
        return
      }
      setUploading(true)
      try {
        const record = await ingestApi.uploadDataset(file)
        await loadPreview(record)
        await refreshRecent()
        if (record.reused) {
          // Idempotent retry: the identical file was uploaded before.
          setUploadError(null)
        }
      } catch (err) {
        const hint =
          err.status === 413
            ? ' — the file is over the 5 MB / 5,000-row caps.'
            : err.status === 422
              ? ' — check that this is a non-empty CSV with a unique header row.'
              : ''
        setUploadError(`Upload failed: ${err.message}${hint}`)
      } finally {
        setUploading(false)
      }
    },
    [loadPreview, refreshRecent],
  )

  const runPipeline = useCallback(async () => {
    if (!dataset) return
    setRunError(null)
    setRunning(true)
    try {
      const { run_id } = await ingestApi.triggerRun(`upload_${dataset.dataset_id}`, contractName)
      if (!run_id) throw new Error('run did not start')
      // The existing Run Detail view owns everything from here.
      navigate(`/runs/${encodeURIComponent(run_id)}`)
    } catch (err) {
      const hint =
        err.status === 422
          ? ' — the contract may not match this dataset, or the dataset was deleted.'
          : err.status === 503
            ? ' — the pipeline source store is temporarily unavailable; try again.'
            : ''
      setRunError(`Pipeline trigger failed: ${err.message}${hint}`)
    } finally {
      setRunning(false)
    }
  }, [dataset, contractName])

  const removeDataset = useCallback(
    async (datasetId) => {
      setDeleting(datasetId)
      try {
        await ingestApi.deleteDataset(datasetId)
        if (dataset?.dataset_id === datasetId) {
          setDataset(null)
          setPreview({ rows: [], rowsReturned: 0, truncated: false })
        }
        await refreshRecent()
      } catch (err) {
        setUploadError(`Delete failed: ${err.message}`)
      } finally {
        setDeleting(null)
      }
    },
    [dataset, refreshRecent],
  )

  const onDrop = (event) => {
    event.preventDefault()
    setDragging(false)
    acceptFile(event.dataTransfer?.files?.[0])
  }
  const onDragOver = (event) => {
    event.preventDefault()
    setDragging(true)
  }
  const selectedContract = contracts.find((c) => c.name === contractName) ?? null

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h1 className="text-sm font-semibold uppercase tracking-wider text-ink">Data ingestion</h1>
          <p className="mt-1 text-[11px] text-ink-faint">
            Upload a bounded CSV, preview it, pick a contract, and run the existing pipeline.
          </p>
        </div>
        <p className="text-[11px] text-ink-faint">caps: 5 MB · 5,000 rows (enforced server-side)</p>
      </header>

      <ErrorBanner title="Upload error" message={uploadError} onDismiss={() => setUploadError(null)} />

      <section
        onDrop={onDrop}
        onDragOver={onDragOver}
        onDragLeave={() => setDragging(false)}
        onClick={() => fileInputRef.current?.click()}
        className={`cursor-pointer rounded-lg border border-dashed p-6 text-center transition-colors ${
          dragging ? 'border-signal bg-signal/10' : 'border-line-strong bg-surface hover:border-ink-faint'
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(event) => {
            acceptFile(event.target.files?.[0])
            event.target.value = ''
          }}
        />
        <p className="text-xs text-ink">
          {uploading ? 'uploading…' : 'Drop a CSV here, or click to choose a file'}
        </p>
        <p className="mt-1 text-[11px] text-ink-faint">
          the exact bytes + a SHA-256 digest are preserved in the dataset store
        </p>
      </section>

      {previewError && <ErrorBanner title="Preview error" message={previewError} />}

      <DatasetPreview
        dataset={dataset}
        rows={preview.rows}
        rowsReturned={preview.rowsReturned}
        truncated={preview.truncated}
        loading={previewLoading}
        error={null}
      />

      <section className="rounded-lg border border-line bg-surface p-3">
        <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-ink-muted">
          2 · Registered data contract
        </h3>
        {contractsError ? (
          <p className="text-xs text-fail">Contracts unavailable: {contractsError}</p>
        ) : contracts.length === 0 ? (
          <p className="text-xs text-ink-faint">no contracts registered yet — upload one on the Contracts page</p>
        ) : (
          <>
            <select
              value={contractName}
              onChange={(event) => {
                setContractName(event.target.value)
                setRunError(null)
              }}
              className="w-full max-w-md rounded border border-line-strong bg-surface-raised px-2 py-1.5 text-sm text-ink focus:border-ink-faint focus:outline-none"
            >
              {contracts.map((contract) => (
                <option key={contract.name} value={contract.name}>
                  {contract.name} (v{contract.version ?? '?'}) — {contract.columns?.length ?? 0} columns
                </option>
              ))}
            </select>
            {selectedContract && (
              <div className="mt-2 rounded border border-line bg-surface-raised p-2">
                <p className="text-[11px] text-ink-faint">
                  Running will validate <span className="text-signal">{dataset?.filename ?? 'your upload'}</span> against{' '}
                  <span className="text-signal">{selectedContract.name}</span> v{selectedContract.version ?? '?'}:
                </p>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {(selectedContract.columns ?? []).map((column) => (
                    <span
                      key={column}
                      className="rounded-full border border-line-strong px-2.5 py-0.5 text-[11px] text-ink-muted"
                    >
                      {column}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </section>

      <ErrorBanner title="Pipeline error" message={runError} onDismiss={() => setRunError(null)} />

      <section className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] text-ink-faint">
          {dataset
            ? `source ID for this run: upload_${dataset.dataset_id}`
            : 'upload a dataset to enable the pipeline run'}
        </p>
        <button
          onClick={runPipeline}
          disabled={!dataset || running || !contractName}
          className="rounded border border-signal bg-signal/15 px-3 py-1.5 text-xs font-medium text-signal hover:bg-signal/25 disabled:opacity-50"
        >
          {running ? 'starting pipeline…' : 'Run pipeline'}
        </button>
      </section>

      <section className="rounded-lg border border-line bg-surface p-3">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">Recent datasets</h3>
          <button onClick={refreshRecent} className="text-[11px] text-ink-faint hover:text-ink">
            refresh
          </button>
        </div>
        {recentError && <p className="text-xs text-fail">Recent datasets unavailable: {recentError}</p>}
        {!recentError && recent.length === 0 && (
          <p className="text-xs text-ink-faint">no datasets uploaded yet</p>
        )}
        {recent.length > 0 && (
          <ul className="divide-y divide-line font-mono text-xs">
            {recent.map((item) => (
              <li key={item.dataset_id} className="flex items-center justify-between gap-3 py-1.5">
                <span className="min-w-0 flex-1 truncate text-ink-muted" title={item.filename}>
                  {item.filename}
                  <span className="ml-2 text-[11px] text-ink-faint">
                    {humanSize(item.byte_size)} · {item.row_count.toLocaleString()} rows · {item.columns?.length ?? 0} cols
                  </span>
                </span>
                <span className="flex shrink-0 gap-1.5">
                  <button
                    onClick={() => loadPreview(item)}
                    className="rounded border border-line-strong px-2 py-0.5 text-[11px] text-ink-muted hover:text-ink"
                  >
                    preview
                  </button>
                  <button
                    onClick={() => removeDataset(item.dataset_id)}
                    disabled={deleting === item.dataset_id}
                    className="rounded border border-line-strong px-2 py-0.5 text-[11px] text-ink-faint hover:text-fail disabled:opacity-50"
                  >
                    {deleting === item.dataset_id ? 'deleting…' : 'delete'}
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
