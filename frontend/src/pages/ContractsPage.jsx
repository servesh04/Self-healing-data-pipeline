import { useCallback, useEffect, useRef, useState } from 'react'
import { deleteContract, getContract, listContracts } from '../api/contracts'
import ContractsList from '../components/Contracts/ContractsList'
import ContractUploader from '../components/Contracts/ContractUploader'
import ContractDetail from '../components/Contracts/ContractDetail'

/**
 * Data Contracts page (route `/contracts`).
 *
 * Self-contained: no shell store, no shared api/client import, no navigation.
 * File contracts from config/contracts are read-only; uploaded contracts are
 * validated by the backend and stored in Postgres. Registered names are
 * immediately selectable in the run trigger because the backend reloads its
 * registry overlay on register/delete.
 */
export default function ContractsPage() {
  const [contracts, setContracts] = useState([])
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState(null)

  const [selectedName, setSelectedName] = useState(null)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState(null)

  const [uploadOpen, setUploadOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState(null)

  // Guards against a slow earlier detail request overwriting a newer selection.
  const detailRequest = useRef(0)

  const loadDetail = useCallback(async (name) => {
    if (!name) {
      setSelectedName(null)
      setDetail(null)
      setDetailError(null)
      return
    }
    const requestId = detailRequest.current + 1
    detailRequest.current = requestId
    setSelectedName(name)
    setDetailLoading(true)
    setDetailError(null)
    setDeleteError(null)
    try {
      const result = await getContract(name)
      if (detailRequest.current === requestId) setDetail(result)
    } catch (err) {
      if (detailRequest.current === requestId) {
        setDetail(null)
        setDetailError(err.message)
      }
    } finally {
      if (detailRequest.current === requestId) setDetailLoading(false)
    }
  }, [])

  const refreshList = useCallback(
    async (preferredName) => {
      try {
        const { contracts: rows } = await listContracts()
        setContracts(rows)
        setListError(null)
        const fallback = rows.find((row) => row.name === 'default') ?? rows[0]
        const target = rows.some((row) => row.name === preferredName)
          ? preferredName
          : fallback?.name ?? null
        await loadDetail(target)
        return rows
      } catch (err) {
        setListError(err.message)
        return []
      }
    },
    [loadDetail],
  )

  useEffect(() => {
    let active = true
    setLoading(true)
    refreshList(null).finally(() => {
      if (active) setLoading(false)
    })
    return () => {
      active = false
    }
  }, [refreshList])

  async function handleRegistered(contract) {
    setUploadOpen(false)
    await refreshList(contract?.name)
  }

  async function handleDelete(name) {
    setDeleting(true)
    setDeleteError(null)
    try {
      await deleteContract(name)
      await refreshList(null)
    } catch (err) {
      setDeleteError(err.message)
    } finally {
      setDeleting(false)
    }
  }

  function handleSelect(name) {
    setUploadOpen(false)
    loadDetail(name)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
            Data contracts
          </h2>
          <p className="mt-1 max-w-2xl text-[11px] text-ink-faint">
            The expected schema a pipeline run is validated against. Upload YAML or JSON,
            review every column constraint, then register it. Uploaded contracts live in
            Postgres with a SHA-256 version; bundled file contracts stay immutable.
          </p>
        </div>
        <button
          onClick={() => setUploadOpen((open) => !open)}
          className="rounded border border-line-strong bg-surface-raised px-3 py-1.5 text-xs font-medium text-ink hover:border-signal-dim"
        >
          {uploadOpen ? 'Close upload' : '+ Upload contract'}
        </button>
      </div>

      {listError && (
        <p role="alert" className="rounded border border-fail/40 bg-fail/10 px-2 py-1.5 text-xs text-fail">
          {listError}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[320px_1fr]">
        <ContractsList
          contracts={contracts}
          selectedName={selectedName}
          loading={loading}
          onSelect={handleSelect}
        />

        <div className="min-w-0 space-y-4">
          {uploadOpen && (
            <ContractUploader
              onRegistered={handleRegistered}
              onCancel={() => setUploadOpen(false)}
            />
          )}

          {detailLoading && !detail && (
            <p className="rounded-lg border border-line bg-surface p-4 text-xs text-ink-faint">
              loading contract…
            </p>
          )}

          {detailError && !detailLoading && (
            <p
              role="alert"
              className="rounded-lg border border-fail/40 bg-fail/10 p-3 text-xs text-fail"
            >
              {detailError}
            </p>
          )}

          {detail && !detailError && (
            <ContractDetail
              detail={detail}
              onDelete={handleDelete}
              deleting={deleting}
              error={deleteError}
            />
          )}

          {!uploadOpen && !detail && !detailLoading && !detailError && (
            <p className="rounded-lg border border-line bg-surface p-4 text-xs text-ink-faint">
              Select a contract to inspect its columns and versions, or upload a new one.
            </p>
          )}
        </div>
      </div>

      <p className="text-[11px] text-ink-faint">
        Tip: registered contracts appear in the contract selector used by “New run”.
        Source selection for an uploaded contract follows the same contract_name
        resolution as every other run.
      </p>
    </div>
  )
}
