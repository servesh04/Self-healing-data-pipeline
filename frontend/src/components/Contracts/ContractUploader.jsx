import { useState } from 'react'
import { MAX_CONTRACT_BYTES, previewContract, registerContract } from '../../api/contracts'
import ContractPreview from './ContractPreview'

const NAME_PATTERN = /^[a-z][a-z0-9_-]*$/

function suggestName(filename) {
  return (filename ?? '')
    .replace(/\.[^.]+$/, '')
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^[^a-z]+/, '')
    .slice(0, 64)
}

function formatBytes(bytes) {
  if (bytes == null) return ''
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KiB`
}

/**
 * Upload → server-side validate → preview → register.
 *
 * The browser never parses the contract itself: the backend is the single
 * validator (`_validated_spec` / `contract_from_snapshot`) and the preview is
 * exactly what would be stored. Nothing here can execute uploaded content.
 */
export default function ContractUploader({ onRegistered, onCancel }) {
  const [file, setFile] = useState(null)
  const [name, setName] = useState('')
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [registering, setRegistering] = useState(false)
  const [error, setError] = useState(null)

  const nameValid = NAME_PATTERN.test(name)
  const canPreview = Boolean(file) && nameValid && !previewing && !registering
  const canRegister = Boolean(preview) && preview.name_available !== false && !registering

  function resetPreview(nextName) {
    setPreview(null)
    setError(null)
    setName(nextName)
  }

  function handleFile(event) {
    const selected = event.target.files?.[0] ?? null
    setPreview(null)
    setError(null)
    if (!selected) {
      setFile(null)
      return
    }
    if (selected.size > MAX_CONTRACT_BYTES) {
      setFile(null)
      setError(`file is ${formatBytes(selected.size)}; the limit is ${formatBytes(MAX_CONTRACT_BYTES)}`)
      return
    }
    setFile(selected)
    if (!name) setName(suggestName(selected.name))
  }

  async function handlePreview() {
    if (!canPreview) return
    setPreviewing(true)
    setError(null)
    try {
      const result = await previewContract(file, name)
      setPreview(result)
      if (result.name) setName(result.name)
    } catch (err) {
      setPreview(null)
      setError(err.message)
    } finally {
      setPreviewing(false)
    }
  }

  async function handleRegister() {
    if (!canRegister) return
    setRegistering(true)
    setError(null)
    try {
      const contract = await registerContract(file, name)
      onRegistered?.(contract)
    } catch (err) {
      setError(err.message)
      setRegistering(false)
    }
  }

  return (
    <section className="rounded-lg border border-line bg-surface p-4">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
            Upload contract
          </h3>
          <p className="mt-1 text-[11px] text-ink-faint">
            YAML or JSON. Validated against the pipeline's contract specification, then stored in Postgres — never on disk.
          </p>
        </div>
        <button
          onClick={onCancel}
          className="rounded border border-line-strong px-2 py-1 text-[11px] text-ink-muted hover:text-ink"
        >
          Cancel
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_1fr]">
        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-faint">Contract file</span>
          <input
            type="file"
            accept=".yaml,.yml,.json,application/yaml,application/json,text/yaml"
            onChange={handleFile}
            className="block w-full text-xs text-ink-muted file:mr-3 file:rounded file:border file:border-line-strong file:bg-surface-raised file:px-2 file:py-1 file:text-xs file:text-ink hover:file:border-signal-dim"
          />
          {file && (
            <span className="mt-1 block text-[10px] text-ink-faint">
              {file.name} · {formatBytes(file.size)}
            </span>
          )}
        </label>

        <label className="block">
          <span className="mb-1 block text-[11px] text-ink-faint">Register as</span>
          <input
            value={name}
            onChange={(event) => resetPreview(event.target.value)}
            placeholder="orders_v2"
            spellCheck={false}
            className={`w-full rounded border bg-surface-raised px-2 py-1.5 font-mono text-xs text-ink focus:outline-none ${
              name && !nameValid ? 'border-fail' : 'border-line-strong focus:border-ink-faint'
            }`}
          />
          <span className="mt-1 block text-[10px] text-ink-faint">
            lowercase letters, digits, _ and -; must start with a letter
          </span>
        </label>
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded border border-fail/40 bg-fail/10 px-2 py-1.5 text-xs text-fail">
          {error}
        </p>
      )}

      {preview && (
        <div className="mt-3 space-y-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-pass/30 bg-pass/10 px-2 py-1.5 text-[11px]">
            <span className="font-medium text-pass">valid contract</span>
            <span className="text-ink-muted">format: {preview.format}</span>
            <span className="text-ink-muted">columns: {preview.column_count}</span>
            <span className="text-ink-muted">row_count_min: {preview.row_count_min}</span>
            <span className="truncate font-mono text-ink-faint" title={preview.version}>
              sha256 {preview.version?.slice(0, 16)}…
            </span>
          </div>
          {preview.name_available === false && (
            <p className="text-xs text-fail">
              “{preview.name}” is already registered or reserved by a file contract. Choose another name.
            </p>
          )}
          <ContractPreview columns={preview.column_preview} />
        </div>
      )}

      <div className="mt-4 flex items-center justify-end gap-2">
        <button
          onClick={handlePreview}
          disabled={!canPreview}
          className="rounded border border-line-strong bg-surface-raised px-3 py-1.5 text-xs font-medium text-ink hover:border-signal-dim disabled:opacity-50"
        >
          {previewing ? 'validating…' : 'Preview'}
        </button>
        <button
          onClick={handleRegister}
          disabled={!canRegister}
          className="rounded border border-signal bg-signal/15 px-3 py-1.5 text-xs font-medium text-signal hover:bg-signal/25 disabled:opacity-50"
        >
          {registering ? 'registering…' : 'Register contract'}
        </button>
      </div>
    </section>
  )
}
