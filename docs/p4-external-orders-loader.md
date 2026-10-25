# P4 external orders loader

This is one concrete integration: a separate `scripts/orders_loader.py`
process stages orders CSV bytes in Postgres, asks the existing agent to
validate and repair that exact batch, and publishes validated rows to the
distinct `published_orders` destination. The agent never writes destination
rows. No generic connector or direct client SQL is exposed by the API.

## Configuration and scope

`config/external_feeds.yaml` allows `partner_orders_a` and
`partner_orders_b`, both using the `default` contract. An external run gets
the mapping namespace `<PIPELINE_ENV>:external:<feed_id>`; every graph
mapping read, compare-and-swap write, rollback, run record and release uses
that namespace. Existing dashboard/default mapping rows are untouched.
The source ID in the run is `feed_id/batch_id`, backed by immutable staged
bytes and the existing `source_snapshots` digest-checked recovery table.

Set these only in the loader process:

| Variable | Meaning |
|---|---|
| `P4_LOADER_DATABASE_URL` | Postgres connection for staging and destination writes |
| `P4_AGENT_API_URL` | Base URL of the agent API |
| `P4_AGENT_TOKEN` | Bearer token for the agent's existing authenticated API |

Use a loader DB role with `INSERT`/`SELECT` on `external_order_batches`,
`INSERT`/`SELECT` on `published_order_batches` and `published_orders`, and
read-only access to `external_order_releases`, `mapping_state` and
`run_records`. The API DB role creates the idempotent schema migration and
alone should have `INSERT` rights on `external_order_releases`. Grant no
loader right to update or delete mapping, release or destination audit rows.
Review role grants before deployment; local development may use a single
owner role, but the safety boundary then rests on process separation.
The backend startup migration creates the staging, authorization and
destination tables before the loader runs; the loader does not need DDL
permission. A failed cold startup can be retried with `/api/ping`.

## Run a batch

From a fresh `PIPELINE_ENV` on the agent backend:

```powershell
python scripts/orders_loader.py submit partner_orders_a batch_20261025 data/sources/day6_ambiguous_rename.csv
```

The loader stages at most 5,000 rows / 10 MiB, rejects a changed payload
under the same `(feed_id, batch_id)`, and calls
`POST /api/external/orders/{feed_id}/{batch_id}/trigger`. This endpoint
accepts only IDs already in the server-side feed registry and staging table.
A transaction claims the batch and stores the run plus snapshot together.
A dispatch lease prevents concurrent duplicate requests from scheduling two
graphs; an expired lease can recover a process crash. Repeated trigger calls
return the same run ID. `GET /api/runs/{run_id}` shows initial validation and
the approval interrupt.

If the run pauses, make an explicit human decision through the loader CLI
(or the existing authenticated run API). The current dashboard run list is
scoped to the default pipeline environment, so feed runs are addressed by
the run ID printed by the loader rather than by that list:

```powershell
python scripts/orders_loader.py approve partner_orders_a batch_20261025 --note "reviewed the rename"
```

After the resumed run completes:

```powershell
python scripts/orders_loader.py retry partner_orders_a batch_20261025
```

The loader requests
`POST /api/external/orders/{feed_id}/{batch_id}/release`. The agent issues
one immutable release authorization only for a terminal `clean` or `healed`
run with passing assertions, matching staged/run digest, and a mapping
version that has not been rolled back. The manifest pins the mapping version,
full mapping, contract hash, feed namespace, run ID and source digest.

The loader rereads the staged bytes, compares their digest and run ID to the
manifest and authorization, compares the mapping to that exact
`mapping_state` version, checks rollback history, independently applies only
supported named mapping operations, and validates every output row before
starting destination inserts. Destination rows and the
`published_order_batches` audit row commit in one transaction. An exact
retry returns `published: false` with the original row count; changed bytes,
run, version or mapping fail closed. A run in `alert`, `escalated`, `running`
or `awaiting_approval` cannot obtain a release.

## Verification

Local tests cover agent failure and approval, graph-to-loader retry,
destination rows, idempotent retry, feed scope separation, unauthorized or
altered manifests, rollback, invalid numeric data, and repair parity across
the named CSV fixtures. The live verification script requires an isolated
backend environment and **writes only its unique batch IDs**:

```powershell
python scripts/verify_p4_orders.py --expected-env p4-my-test-scope
```

It tests a clean control in feed B, an ambiguous rename in feed A that
pauses for synthetic API approval, a healed publish, exact retry with no
duplicate rows, feed B pointer stability, and an unsafe negative that never
publishes. Run it only after checking `/api/runtime` and supplying a test
Postgres role/database. The verifier leaves audit rows in place.

## Limits

The external loader is intentionally orders-only. It supports the current
fixed rename/drop/value/null/date operations and the revenue comma cast;
unknown mapping sections or operations stop publication. Its independent
validator is tested against the agent contract on the supplied fixtures,
and it pins the reviewed default contract SHA-256 version. Future contract
edits stop publication until a new parity review updates that pin. Approval
correctness remains a human decision. The bridge
does not poll or retry on its own; the loader process owns that workflow.
The isolated live Postgres/API/LLM gate passed on 25 October 2026; see
`docs/eval_summary.md`. A database role-grant review remains a release gate
before deploying the loader with separate service credentials. The current
shared bearer token is also embedded in the demo browser client; production
use requires separate service credentials and user authentication.
